"""Validator agent: deterministic checks — no LLM call.

Produces `validation_errors` (strings) and the completeness score that feed
`needs_review`. Hallucination guard: every non-null value must have a
source_span that appears in the source document text.
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
    ) -> tuple[list[str], float, bool]:
        """Returns (validation_errors, completeness_score, needs_review)."""
        errors: list[str] = []

        known = self.catalog.get_fields(doc_type)
        required_names = [f.name for f in known if f.required]
        found_names = {f.name for f in fields}

        # 1) Required catalog fields present?
        missing = [n for n in required_names if n not in found_names]
        for name in missing:
            errors.append(f"Missing required field: {name}")

        # 2) Required fields with empty values?
        by_name = {f.name: f for f in fields}
        for name in required_names:
            field = by_name.get(name)
            if field is not None and field.value in (None, ""):
                errors.append(f"Required field has empty value: {name}")

        # 3) Hallucination guard — evidence must exist in the document.
        for field in fields:
            if document_text and isinstance(field.value, str) and field.value.lstrip().startswith("["):
                errors.extend(_check_array_rows(field, document_text))
                continue
            problem = check_evidence(
                field.name,
                field.value,
                field.source_span,
                document_text,
                is_image_extraction=is_image_extraction,
            )
            if problem:
                errors.append(problem)

        # 4) Low-confidence fields.
        for field in fields:
            if field.value is not None and field.confidence < LOW_CONFIDENCE_THRESHOLD:
                errors.append(f"Low confidence ({field.confidence:.2f}) on {field.name}")

        # 5) Date-typed catalog fields should parse as dates.
        type_by_name = {f.name: (f.type or "string") for f in known}
        for field in fields:
            if field.name.endswith(("_id", "_number", "_code")) and field.value is not None and not isinstance(field.value, str):
                errors.append(f"{field.name}: identifier must be a string preserving all digits")
            if type_by_name.get(field.name) == "date" and isinstance(field.value, str):
                if _try_parse_date(field.value) is None:
                    errors.append(f"Unparseable date for {field.name}: {field.value!r}")

        # Completeness = required fields found with values / total required.
        if required_names:
            filled_required = sum(
                1
                for n in required_names
                if (by_name.get(n) is not None and by_name[n].value not in (None, ""))
            )
            completeness = filled_required / len(required_names)
        else:
            completeness = 1.0

        for table in tables or []:
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
                    problem = check_evidence(label, cell.value, cell.source_span, document_text)
                    if problem:
                        errors.append(problem)
                    if cell.confidence < LOW_CONFIDENCE_THRESHOLD:
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
        needs_review = bool(errors)
        return errors, completeness, needs_review


def _check_array_rows(field: ExtractedField, document_text: str | None) -> list[str]:
    """Legacy arrays lack cell spans; require every cell to exist in the OCR text."""
    if not document_text:
        return []
    try:
        rows = json.loads(str(field.value))
    except (ValueError, TypeError):
        return [f"{field.name}: invalid table JSON"]
    if not isinstance(rows, list):
        return []
    errors = []
    for i, row in enumerate(rows):
        cells = list(row.values()) if isinstance(row, dict) else [row]
        if any(not value_in_text(value, document_text) for value in cells if value is not None):
            errors.append(f"{field.name}: array row {i} contains unsupported values (possible hallucination)")
    return errors
