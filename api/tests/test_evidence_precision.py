"""Precision tests for the evidence guard (review-triage Phase 1).

Locks in the safe direction: false positives fixed (quoted spans, OCR
spacing noise, ISO-normalized dates), true hallucinations still flagged.
Covers check_evidence/value_in_text in app/core/security.py, the required
source_span schema, the numeric-verbatim judge gate, and array-row checks.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.agents.validator import _check_array_rows  # noqa: E402
from app.core.security import (  # noqa: E402
    check_evidence,
    collapse_ocr_spacing,
    is_verbatim_span,
    strip_wrapping_quotes,
    value_in_text,
)
from app.schemas.documents import ExtractedField  # noqa: E402
from app.schemas.llm_schemas import ExtractedFieldEntry  # noqa: E402
from app.services.extraction_service import _all_numeric_spans_verbatim  # noqa: E402


def test_quoted_span_passes_when_value_correct():
    assert check_evidence("po_number", "10256", '"10256"',
                          "Order ID | Date\n10256 | 2016-07-15") is None
    assert check_evidence("invoice_number", "S00012726", "'S00012726'",
                          "Doc No. : S00012726 Date: 13/01/2018") is None


def test_quoted_span_still_flagged_when_value_wrong():
    problem = check_evidence("total_amount", 1900.0, '"Total 1900"',
                             "Total 250 THB. Page 1900 of catalog.")
    assert problem is not None and "hallucination" in problem


def test_strip_wrapping_quotes_only_matched_pairs():
    assert strip_wrapping_quotes('"10256"') == "10256"
    assert strip_wrapping_quotes("'abc'") == "abc"
    assert strip_wrapping_quotes("[10256]") == "10256"
    assert strip_wrapping_quotes("(Paula Parente)") == "Paula Parente"
    assert strip_wrapping_quotes('"unbalanced') == '"unbalanced'
    assert strip_wrapping_quotes('""') == '""'


def test_bracket_wrapped_span_passes_when_value_correct():
    doc = "Order ID | Date\n10256 | 2016-07-15 | Paula Parente"
    assert check_evidence("po_number", "10256", "[10256]", doc) is None


def test_ocr_digit_spacing_tolerated():
    assert collapse_ocr_spacing("201 6-07-15") == "2016-07-15"
    assert is_verbatim_span('"2016-07-15"', "10256 | 201 6-07-15 | Paula") is True
    assert check_evidence("order_date", "2016-07-15", '"2016-07-15"',
                          "10256 | 201 6-07-15 | Paula") is None


def test_iso_normalized_date_passes_against_printed_form():
    assert value_in_text("2016-07-15", "Date: 15/07/2016") is True
    assert check_evidence("order_date", "2016-07-15", "Date: 15/07/2016",
                          "Order 10256 Date: 15/07/2016") is None


def test_wrong_date_still_flagged():
    assert check_evidence("order_date", "2016-07-16", "Date: 15/07/2016",
                          "Order 10256 Date: 15/07/2016") is not None


def test_escaped_newlines_in_span_unescaped():
    span = '"NO 1 JALAN AMAN 2\\nTAMAN DESA 43800"'
    doc = "NO 1 JALAN AMAN 2\nTAMAN DESA 43800\nDENGKIL SELANGOR"
    assert check_evidence("bill_to_address", span.strip('"').replace("\\n", "\n"),
                          span, doc) is None


def test_comma_decimal_separator_matches():
    assert value_in_text("153.5", "Subtotal 153,50") is True
    assert value_in_text(153.5, "Subtotal 153,50") is True
    # Thousands separator still works and must not false-match.
    assert value_in_text(1200.0, "Total 1,200") is True
    assert value_in_text(12.0, "Total 1,200") is False


def test_ocr_coherence_separates_soup_from_documents():
    from app.core.security import is_ocr_text_coherent, ocr_text_coherence

    soup = ("— TAXINVOICE | 86 BELASTINGFAKTUUR | : Bin 2% ๕๕ _ , โญภลทให | ศรเบ | 1 | ๕ "
            ". | อไฮกค ‘ | ed r SB ั 77 /7 | : ( | NA : | B.T.W.Reg Nr ร่ | - ี 3-- ี 33@ "
            "ช 8 ๐ 8 ๐83 เออชชี้เเัั - ัีี้ีืีื้้้ี้ - ี -- เ | [60 | | SIGE | OVC Sard 1 "
            "โอหทร | Subtotaal | Terme V.A.T. inclusive | a ea | pea | จอไก TOTAAL")
    assert ocr_text_coherence(soup) < 0.35
    assert is_ocr_text_coherent(soup) is False

    clean_po = "Purchase Orders\nOrder ID | Date | Customer Name\n10256 | 2016-07-15 | Paula Parente\nProducts\nProduct ID: | Product: | Quantity: | Unit Price:\n53 | Perth Pasties | 15 | 26.2"
    assert is_ocr_text_coherent(clean_po) is True
    # Short texts are exempt — too little signal to judge.
    assert is_ocr_text_coherent("Total 100") is True
    assert is_ocr_text_coherent("") is True
    assert is_ocr_text_coherent(None) is True


def test_value_side_quotes_stripped_before_compare():
    doc = "101870 | 1 | 65.00 | 68.90"
    assert value_in_text("'101870 | 1 |'", doc) is True
    assert value_in_text("[68.90]", doc) is True


def test_ocr_dot_drop_recovers_decimal():
    assert value_in_text(87.45, "Amount 87 45 | SR") is True
    assert value_in_text("87.45", "Amount 87 45 | SR") is True
    # Thousands form must NOT match a small value.
    assert value_in_text(12.0, "Qty 1 200 pcs") is False
    assert value_in_text("12.00", "Qty 1 200 pcs") is False


def test_thai_no_data_placeholder_omitted():
    from app.services.field_catalog import is_placeholder_value

    assert is_placeholder_value("ไม่มีข้อมูล") is True
    assert is_placeholder_value("ไม่ระบุ") is True


def test_short_scattered_span_rejected():
    # Tokens co-occur but never contiguously: must flag (<=3 tokens).
    problem = check_evidence("total_amount", 1900.0, "Total 1900",
                             "Total 250 THB. Page 1900 of catalog.")
    assert problem is not None and "hallucination" in problem


def test_empty_document_text_flags_valued_field():
    problem = check_evidence("total_amount", 5000.0, "Total 5000", "")
    assert problem is not None and "hallucination" in problem
    assert check_evidence("total_amount", 5000.0, "Total 5000", None) is not None
    # Absent values stay unflagged even without text.
    assert check_evidence("total_amount", None, None, "") is None


def test_image_extraction_no_longer_bypasses_verification():
    assert check_evidence("total_amount", 9999.0, "whatever",
                          "INVOICE Total 10", is_image_extraction=True) is not None
    assert check_evidence("total_amount", 10.0, "Total 10",
                          "INVOICE Total 10", is_image_extraction=True) is None


def test_spanless_entry_rejected_by_schema():
    with pytest.raises(Exception):
        ExtractedFieldEntry(name="tax_amount", value="17.5", confidence=0.9,
                            source_span=None)
    with pytest.raises(Exception):
        ExtractedFieldEntry(name="tax_amount", value="17.5", confidence=0.9,
                            source_span="")
    entry = ExtractedFieldEntry(name="tax_amount", value="17.5", confidence=0.9,
                                source_span="Tax 17.5")
    assert entry.source_span == "Tax 17.5"


def test_numeric_verbatim_gate_for_judge_skip():
    latin = "Invoice No: INV-001 Total: 100"
    clean = [
        ExtractedField(name="invoice_number", value="INV-001", confidence=0.95,
                       source_span="Invoice No: INV-001"),
        ExtractedField(name="total_amount", value=100.0, confidence=0.9,
                       source_span="Total: 100"),
    ]
    assert _all_numeric_spans_verbatim(clean, [], latin) is True
    paraphrased = [
        ExtractedField(name="invoice_number", value="INV-001", confidence=0.95,
                       source_span="Invoice No: INV-001"),
        ExtractedField(name="total_amount", value=1900.0, confidence=0.92,
                       source_span="Total 1900"),
    ]
    assert _all_numeric_spans_verbatim(paraphrased, [], "Total 250 THB. Page 1900.") is False
    assert _all_numeric_spans_verbatim(clean, [], "") is False


def test_array_rows_empty_text_flagged():
    import json as _json

    field = ExtractedField(name="line_items",
                           value=_json.dumps([{"description": "x", "quantity": 1}]),
                           confidence=0.9, source_span="x 1")
    assert _check_array_rows(field, "") != []
    assert _check_array_rows(field, None) != []


def test_row_span_cell_grounded_in_document_passes(tmp_path):
    """Row-level span shared across cells: grounded values pass, phantom fails."""
    from app.agents.validator import ValidatorAgent
    from app.schemas.documents import ExtractedTable
    from app.services.field_catalog import FieldCatalog

    doc = "53 | Perth Pasties | 15 | 26.2"
    table = ExtractedTable(
        name="line_items",
        columns=[{"key": k, "label": k} for k in ("product_id", "quantity", "unit_price")],
        rows=[[ {"column": c, "value": v, "confidence": 0.95, "source_span": "53 | Perth Pasties"}
                for c, v in (("product_id", "53"), ("quantity", "15"), ("unit_price", "26.2")) ]],
    )
    errors, _, review = ValidatorAgent(FieldCatalog(tmp_path / "kb")).validate(
        "purchase_order", [], document_text=doc, tables=[table])
    assert [e for e in errors if "line_items" in e] == [], errors
    assert review is False

    table.rows[0][1].value = "999"  # phantom quantity absent from the document
    errors, _, review = ValidatorAgent(FieldCatalog(tmp_path / "kb")).validate(
        "purchase_order", [], document_text=doc, tables=[table])
    assert review is True
    assert any("quantity" in e and "hallucination" in e for e in errors)


def test_info_only_judge_issues_do_not_force_review():
    from app.schemas.documents import JudgeIssue, JudgeResult
    from app.services.extraction_service import _judge_demands_review

    info_only = JudgeResult(score=0.9, issues=[
        JudgeIssue(field="po_number", message="supported", severity="info")], notes="")
    assert _judge_demands_review(info_only) is False
    warned = JudgeResult(score=0.9, issues=[
        JudgeIssue(field="po_number", message="wrong", severity="warning")], notes="")
    assert _judge_demands_review(warned) is True
    low = JudgeResult(score=0.3, issues=[], notes="")
    assert _judge_demands_review(low) is True
    assert _judge_demands_review(None) is False
