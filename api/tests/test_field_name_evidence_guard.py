"""Bug 1 regression: field-name spans are hallucinations, not evidence.

Receipt failure mode (sroie_X51005663293.jpg): the extractor quoted the
catalog key itself (source_span="order_id") instead of the printed value
("593101"). check_evidence() must flag such spans even when the label words
appear somewhere in the document — and the hybrid OCR script selector must
prefer the English reading for Latin-only blocks.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.agents.extractors import _COMMON_RULES  # noqa: E402
from app.core.security import check_evidence, is_field_name_span  # noqa: E402
from app.schemas.documents import ExtractedField  # noqa: E402
from app.services.acceptance import accept_page  # noqa: E402
from app.services.field_catalog import FieldCatalog  # noqa: E402
from app.services.hybrid_ocr import select_script_reading  # noqa: E402

# Mock OCR text shaped like the Sheet Forest Cafe receipt: the value
# "593101" is printed after the "INV No:" label; "Order ID" words exist as
# a nearby caption so a naive verbatim check would pass the field name.
RECEIPT_TEXT = (
    "SHEET FOREST CAFE\n"
    "TAX RECEIPT\n"
    "INV No: 593101\n"
    "Order ID reference for loyalty\n"
    "Nasi Goreng 1 x 12.50 12.50\n"
    "Teh Tarik 2 x 3.00 6.00\n"
    "Total RM 18.50\n"
)

KNOWN = {"order_id", "order id", "inv_no", "inv no", "total"}


def test_source_span_equal_to_field_name_is_hallucination():
    problem = check_evidence("order_id", "593101", "order_id", RECEIPT_TEXT, known_names=KNOWN)
    assert problem is not None
    assert "field name/label" in problem
    assert "hallucination" in problem


def test_source_span_equal_to_column_header_is_hallucination():
    problem = check_evidence("order_id", "593101", "Order ID", RECEIPT_TEXT, known_names=KNOWN)
    assert problem is not None
    assert "hallucination" in problem


def test_field_name_span_flagged_even_when_label_in_document():
    # "Order ID" words ARE in RECEIPT_TEXT — a pure verbatim check would
    # pass, but quoting the label is still not value evidence.
    assert "Order ID" in RECEIPT_TEXT
    assert is_field_name_span("Order ID", KNOWN) is True
    problem = check_evidence("order_id", "Order ID", "Order ID", RECEIPT_TEXT, known_names=KNOWN)
    assert problem is not None


def test_genuine_value_span_still_passes_with_known_names():
    assert check_evidence("order_id", "593101", "593101", RECEIPT_TEXT, known_names=KNOWN) is None
    assert check_evidence("order_id", "593101", "INV No: 593101", RECEIPT_TEXT, known_names=KNOWN) is None


def test_guard_inactive_without_known_names():
    # Legacy callers passing no catalog keep the old behavior: a verbatim
    # span passes, a missing span is flagged as not-found.
    assert check_evidence("order_id", "593101", "593101", RECEIPT_TEXT) is None
    problem = check_evidence("order_id", "593101", "order_id", RECEIPT_TEXT)
    assert problem is not None and "hallucination" in problem


def test_extractor_prompt_forbids_field_name_evidence():
    assert "593101" in _COMMON_RULES
    assert "order_id" in _COMMON_RULES
    assert "NOT 'INV No:" in _COMMON_RULES


def test_extractor_prompt_disambiguates_invoice_number():
    # X51008042779.jpg failure: LLM took the shop registration number
    # 002043319-W as invoice_number instead of the INV No. value 1126679.
    assert "INV No./Invoice No." in _COMMON_RULES
    assert "002043319-W" in _COMMON_RULES
    assert "letterhead" in _COMMON_RULES


def _catalog(tmp_path: Path) -> FieldCatalog:
    kb = tmp_path / "kb"
    (kb / "field_catalog").mkdir(parents=True)
    (kb / "field_catalog" / "invoice_fields.json").write_text(json.dumps({
        "doc_type": "invoice",
        "fields": [
            {"name": "order_id", "type": "string", "required": True},
            {"name": "total_amount", "type": "number", "required": False},
        ],
    }), encoding="utf-8")
    return FieldCatalog(kb)


def test_accept_page_rejects_field_name_span(tmp_path):
    fields = [ExtractedField(name="order_id", value="593101", confidence=0.9, source_span="order_id")]
    accepted, _tables, rejected, _issues, _coverage = accept_page(
        "invoice", page_number=1, fields=fields, tables=[],
        page_text=RECEIPT_TEXT, blocks=[], catalog=_catalog(tmp_path),
    )
    assert accepted == []
    assert [r.location for r in rejected] == ["order_id"]
    assert "field name/label" in rejected[0].rejection_reason


def test_accept_page_accepts_genuine_value_span(tmp_path):
    fields = [ExtractedField(name="order_id", value="593101", confidence=0.9, source_span="593101")]
    accepted, _tables, rejected, _issues, _coverage = accept_page(
        "invoice", page_number=1, fields=fields, tables=[],
        page_text=RECEIPT_TEXT, blocks=[], catalog=_catalog(tmp_path),
    )
    assert [f.name for f in accepted] == ["order_id"]
    assert not [r for r in rejected if r.location == "order_id"]


# --- hybrid OCR script selection -------------------------------------------


def test_latin_only_th_yields_to_english():
    text, _conf, engine, conflict = select_script_reading("INV No", 0.95, "INV No", 0.70)
    assert text == "INV No" and engine == "rapidocr-en" and conflict is False


def test_digit_only_th_yields_to_english():
    text, _conf, engine, conflict = select_script_reading("593101", 0.95, "593101", 0.60)
    assert text == "593101" and engine == "rapidocr-en" and conflict is False


def test_latin_only_conflict_prefers_english_and_stays_reviewable():
    text, _conf, engine, conflict = select_script_reading("0rder ID", 0.60, "Order ID", 0.75)
    assert (text, engine, conflict) == ("Order ID", "rapidocr-en", True)


def test_thai_script_agreement_stays_thai():
    text, _conf, engine, conflict = select_script_reading("ใบเสร็จ", 0.95, "ใบเสร็จ", 0.40)
    assert (text, engine, conflict) == ("ใบเสร็จ", "rapidocr-th", False)


def test_thai_script_conflict_flags_review():
    text, _conf, engine, conflict = select_script_reading("ใบเสร็จรับเงิน", 0.50, "Receipt", 0.70)
    assert (text, engine, conflict) == ("Receipt", "rapidocr-en", True)


def test_empty_english_keeps_thai():
    text, conf, engine, conflict = select_script_reading("Total", 0.80, "", 0.0)
    assert (text, conf, engine, conflict) == ("Total", 0.80, "rapidocr-th", False)
