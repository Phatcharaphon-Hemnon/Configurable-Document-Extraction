"""Per-cell evidence tests for line_items array rows.

Regression cover for the GL Handicraft receipt (.jpg): photo OCR splits the
table row across lines ("SAFETY PINS BUTTERFLY - S" /
"6.00 BOXS x 17.00 102.00 SR") and the extractor serializes numbers without
trailing zeros ("6.0" vs "6.00"), so whole-row concatenation scored 0.625
overlap against the 0.75 threshold and flagged a real row. Per-cell evidence
must pass real rows while still catching phantom rows.
"""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path

_REPOROOT = Path(__file__).resolve().parents[2]
if str(_REPOROOT) not in sys.path:
    sys.path.insert(0, str(_REPOROOT))

from app.agents.validator import ValidatorAgent, _check_array_rows  # noqa: E402
from app.schemas.documents import ExtractedField  # noqa: E402
from app.services.field_catalog import FieldCatalog  # noqa: E402

RECEIPT_OCR = (
    "GL HANDICRAFT & TAILORING 19, JALAN KANCIL, OFF JALAN PUDU, "
    "55100 KUALA LUMPUR MALAYSIA "
    "Company Reg No. :75495-W GST Reg No. :001948532736 TAX INVOICE "
    "Invoice No.: CS 10012 Date : 20/03/2018 13:01 Cashier # : 01 "
    "RM Code SAFETY PINS BUTTERFLY - S 6.00 BOXS x 17.00 102.00 SR "
    "Subtotal : 102.00 Total Excl. of GST 96.23 Total Incl. of GST 102.00 "
    "Total Amt Rounded 102.00 Payment : 102.00 Change Due : 0.00 "
    "Total Item(s) : 6 GST Summary Amount(RM) Tax(RM) SR @ 6% 96.23 5.77 "
    "THANK YOU PLEASE COME AGAIN"
)


def _catalog(tmp_path: Path) -> FieldCatalog:
    return FieldCatalog(tmp_path / "kb")


def _line_item_field(rows: object, span: str = "SAFETY PINS BUTTERFLY - S 6.00 17.00 102.00") -> ExtractedField:
    value = _json.dumps(rows, ensure_ascii=False) if isinstance(rows, list) else rows
    return ExtractedField(name="line_items", value=value, confidence=0.9, source_span=span)


def test_receipt_fragmented_row_passes(tmp_path):
    """The reported bug: real row split across OCR lines + float coercion."""
    rows = [{"description": "SAFETY PINS BUTTERFLY - S", "quantity": 6.0, "unit_price": 17.0, "amount": 102.0}]
    problems = _check_array_rows(_line_item_field(rows), RECEIPT_OCR)
    assert problems == [], problems


def test_receipt_string_amounts_pass(tmp_path):
    rows = [{"description": "SAFETY PINS BUTTERFLY - S", "quantity": "6.00", "unit_price": "17.00", "amount": "102.00"}]
    assert _check_array_rows(_line_item_field(rows), RECEIPT_OCR) == []


def test_numeric_format_variants_pass(tmp_path):
    """6 == 6.00 == 6.0 by magnitude; never by substring (6 must not match 96.23)."""
    doc = "Widget 6.00 each, total 102.00 for 6 items"
    rows = [{"description": "Widget", "quantity": 6, "unit_price": 6.0, "amount": 102}]
    assert _check_array_rows(_line_item_field(rows, span="Widget 6"), doc) == []


def test_phantom_row_still_flagged(tmp_path):
    rows = [
        {"description": "SAFETY PINS BUTTERFLY - S", "quantity": 6.0, "unit_price": 17.0, "amount": 102.0},
        {"description": "Phantom Drink", "quantity": 9, "unit_price": 999, "amount": 8991},
    ]
    problems = _check_array_rows(_line_item_field(rows), RECEIPT_OCR)
    assert len(problems) == 1
    assert "array row 1" in problems[0]
    assert "hallucination" in problems[0]


def test_phantom_quantity_flagged_not_substring_matched(tmp_path):
    """Quantity 9 must not pass via substring of 96.23/5.77."""
    rows = [{"description": "SAFETY PINS BUTTERFLY - S", "quantity": 9, "unit_price": 17.0, "amount": 102.0}]
    problems = _check_array_rows(_line_item_field(rows), RECEIPT_OCR)
    assert len(problems) == 1 and "array row 0" in problems[0]


def test_all_bad_rows_reported(tmp_path):
    rows = [
        {"description": "Ghost A", "quantity": 11, "unit_price": 111, "amount": 1221},
        {"description": "Ghost B", "quantity": 22, "unit_price": 222, "amount": 4884},
    ]
    problems = _check_array_rows(_line_item_field(rows), RECEIPT_OCR)
    assert len(problems) == 2
    assert "array row 0" in problems[0] and "array row 1" in problems[1]


def test_string_rows_fallback(tmp_path):
    real = _line_item_field(["SAFETY PINS BUTTERFLY - S"], span="SAFETY PINS BUTTERFLY - S")
    assert _check_array_rows(real, RECEIPT_OCR) == []
    phantom = _line_item_field(["Phantom Drink 9 x 999"], span="Phantom Drink")
    problems = _check_array_rows(phantom, RECEIPT_OCR)
    assert len(problems) == 1 and "array row 0" in problems[0]


def test_empty_document_text_defers_to_scalar_check(tmp_path):
    rows = [{"description": "Anything", "quantity": 1, "unit_price": 1, "amount": 1}]
    for empty in ("", None):
        problems = _check_array_rows(_line_item_field(rows), empty)
        assert len(problems) == 1 and "hallucination" in problems[0]


def test_validate_end_to_end_receipt_row_clean(tmp_path):
    """Full ValidatorAgent.validate: receipt row contributes no line_items error."""
    rows = [{"description": "SAFETY PINS BUTTERFLY - S", "quantity": 6.0, "unit_price": 17.0, "amount": 102.0}]
    fields = [_line_item_field(rows, span="SAFETY PINS BUTTERFLY - S Subtotal 102.00")]
    errors, _, _ = ValidatorAgent(_catalog(tmp_path)).validate(
        doc_type="invoice", fields=fields, document_text=RECEIPT_OCR)
    assert not [e for e in errors if "line_items" in e and "row" in e.lower()], errors
