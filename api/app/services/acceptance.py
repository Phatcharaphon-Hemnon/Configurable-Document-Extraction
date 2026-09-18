"""Deterministic acceptance policy for extracted fields and tables.

Accepted data contains ONLY supported candidates. Unsupported candidates are
excluded from `fields`/`tables` (and normal exports) but retained in
`rejected_candidates` with reasons for review/debug.

Policy version: see `ACCEPTANCE_POLICY_VERSION` in schemas/documents.py.
Bump it whenever these rules change — it is part of the result-cache
fingerprint.

Rules (all deterministic, no LLM):
- Labels are never values: a value that is just a printed caption
  ("Client name:", "ที่อยู่", "ยอดรวม") with no populated value is rejected.
  Matching uses catalog-derived labels (exact normalized comparison), never a
  global blacklist alone.
- Catalog types enforced: address cannot satisfy a date; a label cannot
  satisfy an amount; dates must parse; amounts must be numeric.
- Currency requires explicit evidence on the page (code or symbol). Never
  inferred from language, Thai text, or locale.
- Buyer/supplier assignment requires surrounding role evidence. A name found
  somewhere is insufficient. Genuine multi-role cases (same org in both
  roles, each evidenced) are preserved. Exception (v1.1.0): on invoices the
  issuing letterhead at the top of the page satisfies supplier/seller fields
  without a printed role label — a receipt's header IS its seller. Scope is
  invoice-only: a purchase order's letterhead is the BUYER, so header
  position must never satisfy a supplier field there.
- Printed zeros, leading-zero identifiers, and legitimate repeated values are
  preserved (never auto-rejected as placeholders/duplicates).
- Ambiguous dates/amounts/handwriting stay unresolved, never guessed.
- Tables: repeated column labels get unique positional keys (no merging);
  each cell must bind to its row/region + column context (no "value appears
  elsewhere" fallback); invalid structures rejected with reasons; placeholder
  ("ไม่มีข้อมูล") rows never fabricated; document totals never moved into
  item rows; duplicate scalar/table representations collapsed to the table.
  Placeholder CELLS mean absent data (info-level), not unresolved evidence:
  since v1.1.0 a row is withheld only for unresolved cells — placeholder
  cells are omitted and the row keeps its grounded cells. Columns with no
  accepted cell in any row are dropped from the accepted table (info).
- Arithmetic checked only with sufficient operands/semantics.
"""

from __future__ import annotations

import copy
import re
from decimal import Decimal, InvalidOperation
from typing import Literal

from app.core.security import check_evidence, normalize_evidence, strip_wrapping_quotes
from app.schemas.documents import (
    ACCEPTANCE_POLICY_VERSION,
    EvidenceReference,
    ExtractedField,
    ExtractedTable,
    RejectedCandidate,
    StructuredReviewIssue,
    TableCell,
    TableColumn,
)
from app.services.evidence import resolve_evidence
from app.services.field_catalog import is_placeholder_value

__all__ = [
    "ACCEPTANCE_POLICY_VERSION",
    "accept_page",
    "is_label_like",
    "catalog_label_set",
]

# ISO 4217 codes seen in this deployment plus printed symbols. "RM" is the
# Malaysian Ringgit mark printed on the mixed-locale receipts.
KNOWN_CURRENCIES = {
    "THB", "USD", "EUR", "PHP", "MYR", "RM", "SGD", "JPY", "CNY", "GBP",
    "AUD", "INR", "KRW", "VND", "LAK", "KHR", "MMK", "IDR", "MYR", "฿",
    "$", "€", "£", "¥",
}

BUYER_ROLE_HINTS = {
    "buyer", "customer", "bill to", "bill_to", "ship to", "client",
    "purchaser", "ผู้ซื้อ", "ลูกค้า", "ผู้สั่งซื้อ", "ชื่อผู้ซื้อ",
    "ที่อยู่ผู้ซื้อ", "ชื่อของคู่ค้า",
}
SUPPLIER_ROLE_HINTS = {
    "supplier", "vendor", "seller", "merchant", "from", "ผู้จำหน่าย",
    "ผู้ขาย", "บริษัทผู้ขาย", "ที่อยู่ผู้ขาย", "ชื่อผู้ขาย",
}

# Invoice letterhead supplier (v1.1.0): on a receipt/tax invoice the
# organization printed at the top of the page is the seller by document
# convention, even with no printed role label ("Seller:", "ผู้ขาย").
# Scope is deliberately invoice-only — a purchase order's letterhead is the
# BUYER (the issuing org), so header position must never satisfy a supplier
# field there. "Top of page" is measured in normalized OCR reading order
# (Tesseract emits blocks top-to-bottom), first SUPPLIER_HEADER_CHARS chars.
SUPPLIER_HEADER_DOC_TYPES = frozenset({"invoice"})
SUPPLIER_HEADER_CHARS = 400

ROLE_FIELDS_BUYER = {"buyer_name", "buyer_address", "bill_to_name", "bill_to_address"}
ROLE_FIELDS_SUPPLIER = {"supplier_name", "supplier_address", "seller_name", "seller_address"}

TOTAL_KEYWORDS = {"total", "grand total", "amount due", "ยอดรวม", "ยอดรวมทั้งสิ้น", "รวม"}

PLACEHOLDER_THAI_EXTRA = {"ไม่มีข้อมูล", "ไม่ระบุ"}


def _strip_label_punct(s: str) -> str:
    return re.sub(r"[\s:：;,.·\-_]+$", "", s.strip()).strip()


def catalog_label_set(doc_type: str, catalog) -> set[str]:
    """Normalized catalog-derived labels for one doc type.

    Includes field names (underscores → spaces), Thai labels/descriptions,
    and their colon-stripped forms. Used for label-as-value detection —
    never a standalone global blacklist.
    """
    labels: set[str] = set()
    try:
        fields = catalog.get_fields(doc_type)
    except Exception:
        return labels
    for f in fields:
        for raw in (f.name, getattr(f, "label_th", None), getattr(f, "description_th", None)):
            if not raw:
                continue
            text = str(raw).strip()
            if not text:
                continue
            labels.add(normalize_evidence(text))
            labels.add(normalize_evidence(_strip_label_punct(text)))
            labels.add(normalize_evidence(text.replace("_", " ")))
            labels.add(normalize_evidence(_strip_label_punct(text.replace("_", " "))))
    return labels


def is_label_like(value: object, labels: set[str]) -> bool:
    """True when the value is just a printed caption (no populated content)."""
    if not isinstance(value, str):
        return False
    v = normalize_evidence(_strip_label_punct(value))
    if not v:
        return True
    return v in labels


def _parse_amount(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    if isinstance(value, str):
        s = value.strip().replace(",", "")
        # Preserve printed zeros: "0" and "0.00" are valid amounts.
        if re.fullmatch(r"-?\d+(\.\d+)?", s):
            try:
                return Decimal(s)
            except InvalidOperation:
                return None
    return None


def _parse_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    from app.services.date_formats import KNOWN_DATE_FORMATS

    s = value.strip()
    if not s or re.fullmatch(r"\d+", s):
        return False
    from datetime import datetime

    for fmt in KNOWN_DATE_FORMATS:
        try:
            datetime.strptime(s, fmt)
            return True
        except ValueError:
            continue
    return False


def _currency_evidence_present(page_text: str | None, source_span: str | None, value: object) -> bool:
    """Explicit currency mark required on the page or in the span."""
    if not isinstance(value, str) or not value.strip():
        return False
    code = value.strip().upper()
    # Symbols match literally; codes match as standalone tokens.
    hay = f"{page_text or ''}\n{source_span or ''}"
    hay_norm = normalize_evidence(hay)
    if code in {"฿", "$", "€", "£", "¥"}:
        return code in hay
    return bool(re.search(r"(?<!\w)" + re.escape(normalize_evidence(code)) + r"(?!\w)", hay_norm))


def _role_evidence_present(value: object, page_text: str | None, hints: set[str], window: int = 240) -> bool:
    """Role keyword must appear near the value (not just somewhere)."""
    if not isinstance(value, str) or not value.strip() or not page_text:
        return False
    hay = normalize_evidence(page_text)
    val = normalize_evidence(strip_wrapping_quotes(value))
    if not val:
        return False
    # Find value occurrences; check a character window around each.
    start = 0
    while True:
        idx = hay.find(val, start)
        if idx < 0:
            return False
        context = hay[max(0, idx - window): idx + len(val) + window]
        if any(normalize_evidence(h) in context for h in hints):
            return True
        start = idx + max(1, len(val))
        if start >= len(hay):
            return False


def _supplier_header_evidence(value: object, page_text: str | None) -> bool:
    """Invoice letterhead: value sits at the top of the page (reading order).

    Deterministic position check on normalized OCR text — the first
    occurrence of the value within SUPPLIER_HEADER_CHARS counts as the
    letterhead region. Never invents or moves evidence; the value must
    already have resolved page-locally (checked before this runs).

    Printed-caption guard: a value that is itself a buyer-role word
    ("Client name:") or ends with a label colon is a caption, never a
    supplier name — the header position must not launder it into one.
    """
    if not isinstance(value, str) or not value.strip() or not page_text:
        return False
    raw = strip_wrapping_quotes(value).strip()
    if raw.endswith(":") or raw.endswith("："):
        return False
    val = normalize_evidence(raw)
    if not val:
        return False
    if any(normalize_evidence(h) in val for h in BUYER_ROLE_HINTS):
        return False
    hay = normalize_evidence(page_text)
    idx = hay.find(val)
    return 0 <= idx < SUPPLIER_HEADER_CHARS


def _dedupe_issues(issues: list[StructuredReviewIssue]) -> list[StructuredReviewIssue]:
    seen: set[tuple] = set()
    out: list[StructuredReviewIssue] = []
    for i in issues:
        key = (i.category, i.target, i.severity, i.evidence or "", i.explanation)
        if key in seen:
            continue
        seen.add(key)
        out.append(i)
    return out


def _reject(
    rejected: list[RejectedCandidate],
    issues: list[StructuredReviewIssue],
    *,
    kind: Literal["field", "cell", "row", "table"],
    location: str,
    value: object,
    confidence: float,
    span: str | None,
    refs: list[EvidenceReference],
    reason: str,
    findings: list[str],
    category: str = "unsupported",
    severity: str = "warning",
) -> None:
    try:
        conf = float(confidence)
    except Exception:
        conf = 0.0
    rejected.append(
        RejectedCandidate(
            candidate_id=f"{kind}:{location}",
            kind=kind,
            location=location,
            proposed_value=value if isinstance(value, (str, float, int)) or value is None else str(value),
            confidence=max(0.0, min(1.0, conf)),
            raw_evidence=span,
            source_refs=list(refs or []),
            rejection_reason=reason,
            validation_findings=list(findings or []),
        )
    )
    issues.append(
        StructuredReviewIssue(
            category=category,  # type: ignore[arg-type]
            target=f"{kind}:{location}",
            severity=severity,  # type: ignore[arg-type]
            evidence=span,
            explanation=reason,
        )
    )


def _normalize_table_columns(table: ExtractedTable) -> tuple[ExtractedTable, list[str]]:
    """Unique positional keys for repeated labels; order + labels preserved."""
    out = copy.deepcopy(table)
    findings: list[str] = []
    seen: dict[str, int] = {}
    new_cols: list[TableColumn] = []
    rename: dict[str, str] = {}
    for col in out.columns:
        base = (col.key or "").strip() or "column"
        norm = re.sub(r"\s+", "_", base.strip().lower()).strip("_") or "column"
        count = seen.get(norm, 0) + 1
        seen[norm] = count
        new_key = norm if count == 1 else f"{norm}__{count}"
        if new_key != col.key:
            findings.append(f"column '{col.key}' renamed to positional key '{new_key}'")
            rename[col.key] = new_key
        new_cols.append(TableColumn(key=new_key, label=col.label))
    out.columns = new_cols
    if rename:
        for row in out.rows:
            for cell in row:
                if cell.column in rename:
                    cell.column = rename[cell.column]
    return out, findings


def accept_page(
    doc_type: str,
    *,
    page_number: int,
    fields: list[ExtractedField],
    tables: list[ExtractedTable],
    page_text: str | None,
    blocks=None,
    catalog=None,
    ocr_uncertain: bool = False,
) -> tuple[list[ExtractedField], list[ExtractedTable], list[RejectedCandidate], list[StructuredReviewIssue], float]:
    """Split candidates into accepted data vs rejected review items.

    Returns (accepted_fields, accepted_tables, rejected, issues, coverage).
    Coverage counts accepted populated required fields only.
    """
    blocks = list(blocks or [])
    labels = catalog_label_set(doc_type, catalog) if catalog is not None else set()
    type_by_name: dict[str, str] = {}
    required: list[str] = []
    if catalog is not None:
        try:
            for f in catalog.get_fields(doc_type):
                type_by_name[f.name] = (f.type or "string").lower()
                if f.required:
                    required.append(f.name)
        except Exception:
            pass

    accepted_fields: list[ExtractedField] = []
    accepted_tables: list[ExtractedTable] = []
    rejected: list[RejectedCandidate] = []
    issues: list[StructuredReviewIssue] = []

    has_tables = bool(tables)
    for field in fields or []:
        name = field.name
        value = field.value
        span = field.source_span
        refs = resolve_evidence(span, page_number=page_number, blocks=blocks, page_text=page_text, role="value")

        # Duplicate scalar/table representation: the structured table wins.
        if name == "line_items" and has_tables and isinstance(value, str) and value.lstrip().startswith("["):
            _reject(rejected, issues, kind="field", location=name, value=value,
                    confidence=field.confidence, span=span, refs=refs,
                    reason="duplicate scalar/table representation: structured table retained, scalar excluded",
                    findings=["duplicate representation"], category="mechanical", severity="info")
            continue

        # Placeholder values (incl. Thai "ไม่มีข้อมูล") are never accepted.
        if is_placeholder_value(value) or (isinstance(value, str) and value.strip() in PLACEHOLDER_THAI_EXTRA):
            _reject(rejected, issues, kind="field", location=name, value=value,
                    confidence=field.confidence, span=span, refs=refs,
                    reason="placeholder value means absent: omitted from accepted data",
                    findings=["placeholder value"], category="unsupported", severity="info")
            continue

        # Evidence must resolve page-locally (no "appears elsewhere" fallback).
        # Reason preserves the legacy check_evidence phrasing ("no source_span",
        # "possible hallucination") so existing consumers/tests keep working.
        if value is not None and not refs:
            problem = check_evidence(name, value, span, page_text)
            reason = problem or "unsupported: no page-local source reference resolves"
            _reject(rejected, issues, kind="field", location=name, value=value,
                    confidence=field.confidence, span=span, refs=refs,
                    reason=reason,
                    findings=[problem or reason],
                    category="unsupported")
            continue
        problem = check_evidence(name, value, span, page_text)
        if problem:
            cat = "mechanical" if "not supported by source_span" in problem else "unsupported"
            _reject(rejected, issues, kind="field", location=name, value=value,
                    confidence=field.confidence, span=span, refs=refs,
                    reason=problem, findings=[problem], category=cat)
            continue

        # Labels are never values.
        if isinstance(value, str) and is_label_like(value, labels):
            reason = f"label used as value: {value!r} is a printed caption with no populated value"
            _reject(rejected, issues, kind="field", location=name, value=value,
                    confidence=field.confidence, span=span, refs=refs,
                    reason=reason, findings=[reason], category="semantic")
            continue

        # Catalog type enforcement.
        ctype = type_by_name.get(name, "string")
        if value is not None:
            if ctype == "date" and isinstance(value, str) and not _parse_date(value):
                reason = f"Unparseable date for {name}: {value!r}"
                _reject(rejected, issues, kind="field", location=name, value=value,
                        confidence=field.confidence, span=span, refs=refs,
                        reason=reason, findings=[reason], category="type")
                continue
            if ctype in {"number", "amount", "integer", "float"} and _parse_amount(value) is None:
                # A label or address can never satisfy an amount field.
                reason = f"type error: {value!r} is not numeric for amount field {name}"
                _reject(rejected, issues, kind="field", location=name, value=value,
                        confidence=field.confidence, span=span, refs=refs,
                        reason=reason, findings=[reason], category="type")
                continue
            if name == "currency" and not _currency_evidence_present(page_text, span, value):
                reason = f"unsupported currency: {value!r} has no explicit code/symbol evidence on the page; never inferred from locale"
                _reject(rejected, issues, kind="field", location=name, value=value,
                        confidence=field.confidence, span=span, refs=refs,
                        reason=reason, findings=[reason], category="unsupported")
                continue
            if name in ROLE_FIELDS_BUYER and isinstance(value, str):
                if not _role_evidence_present(value, page_text, BUYER_ROLE_HINTS):
                    reason = f"semantic: buyer role for {value!r} lacks surrounding role evidence; name found somewhere is insufficient"
                    _reject(rejected, issues, kind="field", location=name, value=value,
                            confidence=field.confidence, span=span, refs=refs,
                            reason=reason, findings=[reason], category="semantic")
                    continue
            if name in ROLE_FIELDS_SUPPLIER and isinstance(value, str):
                if not _role_evidence_present(value, page_text, SUPPLIER_ROLE_HINTS):
                    # v1.1.0: invoice letterhead fallback — the issuing org
                    # printed at the top of a receipt/tax invoice is the
                    # seller by document convention. Info issue for
                    # transparency; never silently accepted.
                    if doc_type in SUPPLIER_HEADER_DOC_TYPES and _supplier_header_evidence(
                        value, page_text
                    ):
                        issues.append(
                            StructuredReviewIssue(
                                category="semantic",
                                target=f"field:{name}",
                                severity="info",
                                evidence=span,
                                explanation=(
                                    "supplier accepted by invoice letterhead position "
                                    "(top of page, no printed role label)"
                                ),
                            )
                        )
                    else:
                        reason = f"semantic: supplier role for {value!r} lacks surrounding role evidence; name found somewhere is insufficient"
                        _reject(rejected, issues, kind="field", location=name, value=value,
                                confidence=field.confidence, span=span, refs=refs,
                                reason=reason, findings=[reason], category="semantic")
                        continue

        accepted_fields.append(field.model_copy(update={"evidence_refs": refs, "acceptance": "accepted"}))

    # --- Tables ---
    for table in tables or []:
        norm_table, rename_findings = _normalize_table_columns(table)
        for f in rename_findings:
            issues.append(StructuredReviewIssue(category="row_column", target=f"table:{table.name}",
                                                severity="info", explanation=f))
        col_keys = [c.key for c in norm_table.columns]
        if len(col_keys) != len(set(col_keys)):
            _reject(rejected, issues, kind="table", location=table.name, value=None,
                    confidence=0.0, span=None, refs=[],
                    reason="structurally invalid: duplicate column keys after positional normalization",
                    findings=["duplicate column keys"], category="row_column", severity="error")
            continue
        kept_rows: list[list[TableCell]] = []
        for ri, row in enumerate(norm_table.rows):
            loc = f"{norm_table.name}/{ri}"
            row_keys = [c.column for c in row]
            if len(row_keys) != len(set(row_keys)) or set(row_keys) != set(col_keys):
                _reject(rejected, issues, kind="row", location=loc, value=None,
                        confidence=0.0, span=None, refs=[],
                        reason="structurally invalid: row columns missing, duplicated, or unknown",
                        findings=["columns missing, duplicated or unknown"], category="row_column", severity="error")
                continue
            # Placeholder-only rows are never fabricated into accepted data.
            if row and all(is_placeholder_value(c.value) or (isinstance(c.value, str) and c.value.strip() in PLACEHOLDER_THAI_EXTRA) for c in row):
                _reject(rejected, issues, kind="row", location=loc, value=None,
                        confidence=0.0, span=None, refs=[],
                        reason="placeholder row omitted: no populated cells",
                        findings=["placeholder row"], category="unsupported", severity="info")
                continue
            row_ok = True
            accepted_row: list[TableCell] = []
            for cell in row:
                cloc = f"{norm_table.name}/{ri}/{cell.column}"
                if is_placeholder_value(cell.value) or (isinstance(cell.value, str) and cell.value.strip() in PLACEHOLDER_THAI_EXTRA):
                    # v1.1.0: placeholder means ABSENT data (info-level), not
                    # unresolved evidence — it must not withhold the row.
                    # Only unresolved cells (no refs / span mismatch) do.
                    _reject(rejected, issues, kind="cell", location=cloc, value=cell.value,
                            confidence=cell.confidence, span=cell.source_span, refs=[],
                            reason="placeholder cell omitted", findings=["placeholder value"],
                            category="unsupported", severity="info")
                    continue
                refs = resolve_evidence(cell.source_span, page_number=page_number, blocks=blocks,
                                        page_text=page_text, role="value")
                if cell.value is not None and not refs:
                    _reject(rejected, issues, kind="cell", location=cloc, value=cell.value,
                            confidence=cell.confidence, span=cell.source_span, refs=[],
                            reason="unsupported: cell has no page-local source reference; value appearing elsewhere is insufficient",
                            findings=[check_evidence(cloc, cell.value, cell.source_span, page_text) or "no evidence"],
                            category="row_column")
                    row_ok = False
                    continue
                problem = check_evidence(cloc, cell.value, cell.source_span, page_text)
                if problem:
                    _reject(rejected, issues, kind="cell", location=cloc, value=cell.value,
                            confidence=cell.confidence, span=cell.source_span, refs=refs,
                            reason=problem, findings=[problem],
                            category="mechanical" if "not supported" in problem else "unsupported")
                    row_ok = False
                    continue
                # Document totals must not move into item rows.
                span_norm = normalize_evidence(cell.source_span or "")
                if any(kw in span_norm for kw in TOTAL_KEYWORDS) and cell.column.lower() not in {"total", "total_price", "total_amount", "amount"}:
                    # Only flag when the row looks like a totals summary, not a
                    # genuine item that happens to mention "total" nearby.
                    row_spans = normalize_evidence(" ".join(c.source_span or "" for c in row))
                    if sum(1 for kw in TOTAL_KEYWORDS if kw in row_spans) >= 1 and len(row) <= 2:
                        _reject(rejected, issues, kind="cell", location=cloc, value=cell.value,
                                confidence=cell.confidence, span=cell.source_span, refs=refs,
                                reason="wrong-row: document total must not move into item rows",
                                findings=["totals row in item table"], category="row_column")
                        row_ok = False
                        continue
                accepted_row.append(cell.model_copy(update={"evidence_refs": refs, "acceptance": "accepted"}))
            if row_ok and accepted_row:
                kept_rows.append(accepted_row)
            elif not row_ok:
                # Keep the failure visible at row level without accepting partial rows.
                _reject(rejected, issues, kind="row", location=loc, value=None,
                        confidence=0.0, span=None, refs=[],
                        reason="row withheld: one or more cells unresolved (see cell findings)",
                        findings=["unresolved cells"], category="row_column", severity="warning")
        if kept_rows:
            kept = copy.deepcopy(norm_table)
            kept.rows = kept_rows
            # v1.1.0: a column with no accepted cell in any kept row carries
            # no data (all its cells were placeholders) — drop it from the
            # accepted table so exports/renders stay tidy. Info issue only;
            # any evidence failures already surfaced as cell rejections.
            present_cols = {c.column for row in kept_rows for c in row}
            dropped = [c for c in kept.columns if c.key not in present_cols]
            if dropped:
                kept.columns = [c for c in kept.columns if c.key in present_cols]
                for col in dropped:
                    issues.append(
                        StructuredReviewIssue(
                            category="row_column",
                            target=f"table:{table.name}",
                            severity="info",
                            explanation=(
                                f"column '{col.key}' omitted from accepted table: "
                                "no populated cells in any accepted row (all placeholder)"
                            ),
                        )
                    )
            accepted_tables.append(kept)
        else:
            _reject(rejected, issues, kind="table", location=norm_table.name, value=None,
                    confidence=0.0, span=None, refs=[],
                    reason="table withheld: no fully grounded rows",
                    findings=["no accepted rows"], category="row_column", severity="warning")

    if required:
        accepted_names = {f.name for f in accepted_fields if f.value not in (None, "")}
        filled = sum(1 for n in required if n in accepted_names)
        coverage = filled / len(required)
    else:
        coverage = 1.0

    if ocr_uncertain and not any("OCR" in i.explanation for i in issues):
        issues.append(StructuredReviewIssue(category="ocr_ambiguity", target="page",
                                            severity="warning",
                                            explanation="OCR uncertainty preserved for review"))

    return accepted_fields, accepted_tables, rejected, _dedupe_issues(issues), coverage
