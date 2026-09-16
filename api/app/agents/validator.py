"""Validator agent: deterministic checks — no LLM call.

Runs the acceptance policy first (accepted vs rejected + structured issues),
then legacy deterministic checks on ACCEPTED data only. Produces
`validation_errors` (strings) and the completeness score that feed
`needs_review`. Hallucination guard: every non-null value must have a
source_span that appears in the source document text.

Table strictness: no "value appears elsewhere" fallback — a cell is accepted
only when its own source_span resolves page-locally and supports its value.
If the row/region cannot be established, the cell stays unresolved (rejected)
instead of being accepted.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from app.core.security import check_evidence, value_in_text
from app.schemas.documents import ExtractedField, ExtractedTable
from app.services.date_formats import KNOWN_DATE_FORMATS
from app.services.field_catalog import FieldCatalog

LOW_CONFIDENCE_THRESHOLD = 0.6


def _try_parse_date(value: str) -> datetime | None:
    s = value.strip()
    if not s or re.fullmatch(r"\d+", s):
        return None
    for fmt in KNOWN_DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


class ValidatorAgent:
    def __init__(self, catalog: FieldCatalog) -> None:
        self.catalog = catalog

    def validate(
        self,
        doc_type: str,
        fields: list[ExtractedField],
        document_text: str | None = None,
        is_image_extraction: bool = False,
        tables: list[ExtractedTable] | None = None,
        blocks: list | None = None,
        page_number: int = 1,
        ocr_uncertain: bool = False,
    ) -> tuple[list[str], float, bool]:
        """Returns (validation_errors, completeness_score, needs_review)."""
        errors, completeness, needs_review, _, _, _, _ = self.validate_detailed(
            doc_type,
            fields,
            document_text=document_text,
            is_image_extraction=is_image_extraction,
            tables=tables,
            blocks=blocks,
            page_number=page_number,
            ocr_uncertain=ocr_uncertain,
        )
        return errors, completeness, needs_review

    def validate_detailed(
        self,
        doc_type: str,
        fields: list[ExtractedField],
        document_text: str | None = None,
        is_image_extraction: bool = False,
        tables: list[ExtractedTable] | None = None,
        blocks: list | None = None,
        page_number: int = 1,
        ocr_uncertain: bool = False,
    ):
        """Full deterministic pass with acceptance separation.

        Returns (errors, completeness, needs_review, accepted_fields,
        accepted_tables, rejected, issues). Coverage counts accepted
        populated required fields only.
        """
        from app.services.acceptance import accept_page

        accepted_fields, accepted_tables, rejected, issues, coverage = accept_page(
            doc_type,
            page_number=page_number,
            fields=list(fields or []),
            tables=list(tables or []),
            page_text=document_text,
            blocks=blocks,
            catalog=self.catalog,
            ocr_uncertain=ocr_uncertain,
        )

        errors: list[str] = []

        # Rejection reasons feed validation_errors (accepted data stays clean).
        for cand in rejected:
            # Skip info-level duplicates/placeholders from error list? No —
            # keep them visible but distinguishable; needs_review triggers on
            # warning/error only below. Info still listed for transparency.
            errors.append(f"{cand.location}: {cand.rejection_reason}" if ": " not in cand.rejection_reason[:80] else cand.rejection_reason)

        known = self.catalog.get_fields(doc_type)
        required_names = [f.name for f in known if f.required]
        accepted_by_name = {f.name: f for f in accepted_fields}

        # 1) Required catalog fields present in ACCEPTED data?
        for name in required_names:
            if name not in accepted_by_name:
                msg = f"Missing required field: {name}"
                if msg not in errors:
                    errors.append(msg)

        # 2) Required fields with empty values (accepted only)?
        for name in required_names:
            field = accepted_by_name.get(name)
            if field is not None and field.value in (None, ""):
                errors.append(f"Required field has empty value: {name}")

        # Legacy array-scalar check on accepted array fields (no tables path).
        for field in accepted_fields:
            if document_text and isinstance(field.value, str) and field.value.lstrip().startswith("["):
                errors.extend(_check_array_rows(field, document_text))

        # 4) Low-confidence on ACCEPTED fields (model estimate, never replaced).
        for field in accepted_fields:
            try:
                conf = float(field.confidence)
            except Exception:
                conf = 0.0
            if field.value is not None and conf < LOW_CONFIDENCE_THRESHOLD:
                errors.append(f"Low confidence ({conf:.2f}) on {field.name}")

        # 5) Identifier shape + date parse on ACCEPTED (acceptance already
        # rejects bad dates, but keep the legacy message as a second gate).
        type_by_name = {f.name: (f.type or "string") for f in known}
        for field in accepted_fields:
            if field.name.endswith(("_id", "_number", "_code")) and field.value is not None and not isinstance(field.value, str):
                errors.append(f"{field.name}: identifier must be a string preserving all digits")
            if type_by_name.get(field.name) == "date" and isinstance(field.value, str):
                if _try_parse_date(field.value) is None:
                    msg = f"Unparseable date for {field.name}: {field.value!r}"
                    if msg not in errors:
                        errors.append(msg)

        # Tables: accepted tables are fully grounded by construction. Keep
        # structural + arithmetic gates on accepted tables with legacy messages.
        for table in accepted_tables:
            keys = [column.key for column in table.columns]
            if len(keys) != len(set(keys)):
                errors.append(f"{table.name}: duplicate column keys")
            for index, row in enumerate(table.rows):
                row_keys = [cell.column for cell in row]
                if len(row_keys) != len(set(row_keys)) or set(row_keys) != set(keys):
                    errors.append(f"{table.name} row {index + 1}: columns missing, duplicated or unknown")
                for cell in row:
                    label = f"{table.name} row {index + 1} {cell.column}"
                    if cell.value is None:
                        errors.append(f"{label}: unreadable cell")
                    # Strict: no fallback. The cell's own span must support it.
                    problem = check_evidence(label, cell.value, cell.source_span, document_text)
                    if problem:
                        errors.append(problem)
                    try:
                        cconf = float(cell.confidence)
                    except Exception:
                        cconf = 0.0
                    if cconf < LOW_CONFIDENCE_THRESHOLD:
                        errors.append(f"{label}: low confidence")
                values = {c.column: c.value for c in row}
                # Arithmetic only when exact catalog-style keys are present. No aliases.
                if all(isinstance(values.get(k), (float, int)) for k in ("quantity", "unit_price", "total_price")):
                    expected = values["quantity"] * values["unit_price"]
                    discount = values.get("discount_percent")
                    if isinstance(discount, (int, float)):
                        expected *= 1 - discount / 100
                    if abs(expected - values["total_price"]) > .02:
                        errors.append(f"{table.name} row {index + 1}: amount arithmetic mismatch")

        # needs_review triggers on any error except pure-info rejections.
        # Info rejections (placeholder/duplicate-representation) are listed
        # for transparency but do not alone force review.
        has_actionable = False
        for e in errors:
            if "placeholder" in e.lower() or "duplicate scalar/table" in e.lower():
                continue
            has_actionable = True
            break
        # Rejected warning/error candidates always force review even if their
        # message text looks informational.
        if any(getattr(c, "candidate_id", "") and True for c in rejected):
            # Only warning+ rejections force review; info placeholders above
            # are excluded by checking the paired structured issue severity.
            sev_by_target = {i.target: i.severity for i in issues}
            for cand in rejected:
                sev = sev_by_target.get(f"{cand.kind}:{cand.location}", "warning")
                if sev in ("warning", "error"):
                    has_actionable = True
                    break
        needs_review = has_actionable
        return errors, coverage, needs_review, accepted_fields, accepted_tables, rejected, issues


def _check_array_rows(field: ExtractedField, document_text: str | None) -> list[str]:
    """Legacy arrays lack cell spans; require every cell to exist in the OCR text."""
    if not document_text:
        return [f"{field.name}: no document text to verify array rows against (possible hallucination)"]
    try:
        rows = json.loads(str(field.value))
    except (ValueError, TypeError):
        return [f"{field.name}: invalid table JSON"]
    if not isinstance(rows, list):
        return []
    errors = []
    for i, row in enumerate(rows):
        cells = _row_claimed_values(row)
        if any(not value_in_text(value, document_text) for value in cells if value is not None):
            errors.append(f"{field.name}: array row {i} contains unsupported values (possible hallucination)")
    return errors


def _row_claimed_values(row: object) -> list:
    """Values a legacy array row actually claims about the document.

    Dict keys are schema labels, never document content: `column_N` header
    keys and flat cell structs (`column`/`value`/`confidence`/`source_span`)
    must not be verified as values — only the claimed values are checked.
    """
    if not isinstance(row, dict):
        return [row]
    if set(row) >= {"column", "value"}:
        return [row.get("value")]
    return list(row.values())
