"""Thai catalogs + CPU hybrid OCR (mocked, no model downloads).

Covers: Thai metadata round-trips, unchanged English keys, exact matching,
unknown-field registration (Thai labels never duplicate), mixed pages, table
geometry, retry eligibility/limits, conflicting readings, TrOCR provisional
+ review, timeouts/cache/review propagation.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.schemas.documents import ExtractionResult  # noqa: E402
from app.schemas.ocr import OCRBlock, OCRPage  # noqa: E402
from app.services.extraction_service import _merge_ocr_reviews  # noqa: E402
from app.services.field_catalog import FieldCatalog, normalize_field_name  # noqa: E402
from app.services.field_matching import build_alternative_name_lookup  # noqa: E402
from app.services.hybrid_ocr import (  # noqa: E402
    contains_latin,
    contains_thai,
    is_ambiguous_script,
    is_digit_only,
    is_trocr_eligible,
)
from app.services.local_ocr import LocalOCRClient, layout_text  # noqa: E402

KB = _API_DIR / "app" / "data" / "knowledge_base"


def _real_catalog() -> FieldCatalog:
    return FieldCatalog(KB)


# -- catalog ---------------------------------------------------------------

def test_thai_metadata_round_trip():
    catalog = _real_catalog()
    for doc_type in ("invoice", "purchase_order", "delivery_note"):
        fields = catalog.get_fields(doc_type)
        assert fields, doc_type
        for field in fields:
            assert field.label_th and field.label_th.strip(), f"{doc_type}.{field.name} label_th"
            assert field.description_th and field.description_th.strip(), f"{doc_type}.{field.name} description_th"
        compact = catalog.compact_for_prompt(doc_type)
        # English prefix intact + Thai hint present.
        assert " — " in compact
        assert fields[0].name in compact
        assert (fields[0].description_th or fields[0].label_th) in compact


def test_english_keys_types_required_unchanged():
    catalog = _real_catalog()
    invoice = {f.name: f for f in catalog.get_fields("invoice")}
    assert {"invoice_number", "invoice_date", "seller_name", "total_amount"} <= set(invoice)
    assert invoice["invoice_number"].required is True
    assert invoice["invoice_date"].type == "date"
    assert invoice["total_amount"].type == "number"
    po = {f.name: f for f in catalog.get_fields("purchase_order")}
    assert po["po_number"].required is True and po["order_date"].required is True
    assert po["total_amount"].required is False
    dn = {f.name: f for f in catalog.get_fields("delivery_note")}
    assert dn["delivery_note_number"].required is True and dn["delivered_by"].required is True
    assert dn["delivery_date"].required is False
    # No Thai leaks into keys; all keys sane snake_case.
    for doc_type in ("invoice", "purchase_order", "delivery_note"):
        for name in catalog.get_field_names(doc_type):
            assert normalize_field_name(name) == name
            assert not contains_thai(name)


def test_exact_matching_ignores_thai_labels():
    catalog = _real_catalog()
    assert catalog.lookup("invoice", "Invoice Number") is not None
    assert catalog.lookup("invoice", "เลขที่ใบแจ้งหนี้") is None
    assert catalog.lookup("invoice", "grand_total") is None  # aliases never match
    lookup = build_alternative_name_lookup(catalog.get_fields("invoice"))
    assert "invoice_number" in lookup
    assert not any(contains_thai(k) for k in lookup)


def test_thai_display_label_never_registers(tmp_path):
    kb = tmp_path / "kb"
    (kb / "field_catalog").mkdir(parents=True)
    (kb / "field_catalog" / "invoice_fields.json").write_text(json.dumps({
        "doc_type": "invoice",
        "fields": [{"name": "invoice_number", "type": "string", "required": True}],
    }), encoding="utf-8")
    catalog = FieldCatalog(kb)
    from app.schemas.documents import ExtractedField
    from app.services.field_catalog import register_discovered_fields

    outcome = register_discovered_fields(
        catalog, "invoice",
        [ExtractedField(name="เลขที่ใบแจ้งหนี้", value="INV-1", confidence=0.99, source_span="INV-1")],
        document_text="INV-1",
    )
    assert outcome.added == []
    assert outcome.skipped and "snake_case" in outcome.skipped[0]["reason"]
    data = json.loads((kb / "field_catalog" / "invoice_fields.json").read_text(encoding="utf-8"))
    assert all("เลข" not in f["name"] for f in data["fields"])


# -- layout ----------------------------------------------------------------

def test_mixed_thai_english_layout_and_table_gaps():
    blocks = [
        OCRBlock(text="จำนวน", confidence=0.9, box=(0, 0, 20, 10)),
        OCRBlock(text="สินค้า", confidence=0.9, box=(22, 0, 20, 10)),
        OCRBlock(text="ราคา", confidence=0.9, box=(100, 0, 20, 10)),
        OCRBlock(text="ใบกำกับภาษี", confidence=0.95, box=(0, 20, 50, 10)),
        OCRBlock(text="Invoice", confidence=0.9, box=(60, 20, 40, 10)),
    ]
    text = layout_text(blocks)
    assert "จำนวนสินค้า | ราคา" in text
    assert "ใบกำกับภาษี Invoice" in text


# -- eligibility -----------------------------------------------------------

def test_trocr_eligibility_rules():
    assert is_trocr_eligible("Total 100", "Total 100", "Total 100", 0.5, threshold=0.80)
    assert not is_trocr_eligible("Total 100", "Total 100", "Total 100", 0.9, threshold=0.80)
    assert not is_trocr_eligible("ยอดรวม 100", "ยอดรวม 100", None, 0.5, threshold=0.80)
    # Recovery case: Thai-looking TH reading must NOT veto TrOCR when the
    # English candidate supports Latin text.
    assert is_trocr_eligible("Total 100", "ยอดรวม", "Total 100", 0.5, threshold=0.80)
    # No English support, no retry — even for Latin-looking selections.
    assert not is_trocr_eligible("Total 100", "Total 100", None, 0.5, threshold=0.80)
    assert not is_trocr_eligible("Total 100", "Total 100", "", 0.5, threshold=0.80)
    assert not is_trocr_eligible("Total 100", "Total 100", "12345", 0.5, threshold=0.80)
    assert not is_trocr_eligible("", "", "", 0.1, threshold=0.80)
    assert not is_trocr_eligible("12345", "12345", "12345", 0.1, threshold=0.80)
    assert not is_trocr_eligible("Total 发票", "Total 发票", None, 0.1, threshold=0.80)
    assert is_digit_only("123 45")
    assert not is_digit_only("INV 123")
    assert is_ambiguous_script("Total 发票")
    assert not is_ambiguous_script("Total 123")
    assert contains_thai("ใบกำกับ")
    assert contains_latin("Invoice 123")


# -- hybrid orchestration (mocked RapidOCR + TrOCR) -------------------------

def _mock_rapid(th_texts, th_confs, en_map=None):
    """Mock RapidOCRClient with N detection boxes in a row."""
    import numpy as np

    n = len(th_texts)
    boxes = np.zeros((n, 4, 2), dtype=float)
    for i in range(n):
        x = float(i * 100)
        boxes[i] = [[x, 0], [x + 80, 0], [x + 80, 20], [x, 20]]
    image = np.zeros((40, n * 100 + 20, 3), dtype=np.uint8)
    rapid = MagicMock()
    rapid.ocr_detailed.return_value = SimpleNamespace(
        image=image, boxes=boxes, txts_th=list(th_texts), scores_th=list(th_confs))
    rapid.crop_region.side_effect = lambda img, poly: np.zeros((20, 80, 3), dtype=np.uint8)
    rapid.model_revisions.return_value = {"rapidocr_pkg": "3.9.2"}
    en_map = en_map or {}

    def _rec(crops, lang="en"):
        out_t, out_s = [], []
        for _ in crops:
            # Pop per-call overrides in order when provided as lists.
            out_t.append("")
            out_s.append(0.0)
        # Caller sets side_effect per test instead; default empty.
        return out_t, out_s

    rapid.recognize_crops.side_effect = _rec
    return rapid


def _settings(threshold=0.80, max_regions=10):
    return SimpleNamespace(
        hybrid_trocr_conf_threshold=threshold,
        hybrid_trocr_max_regions=max_regions,
        hybrid_trocr_model="microsoft/trocr-base-handwritten",
        hybrid_trocr_revision="aff187bd81f8d73231cd3ed24b7857fcb10ae00e",
        hybrid_preprocess_version="hybrid-v1",
    )


def test_conflicting_rapidocr_readings_retained():
    from app.services.hybrid_ocr import hybrid_ocr_page

    rapid = _mock_rapid(["Total 100"], [0.9])
    # EN disagrees on a non-Thai region → conflict, EN provisional.
    rapid.recognize_crops.side_effect = lambda crops, lang="en": (["Total 700"], [0.85])
    blocks, engines, reviews, _ = hybrid_ocr_page(
        b"img", settings=_settings(), rapid_client=rapid, trocr_client=MagicMock())
    assert blocks and blocks[0].text == "Total 700"
    assert blocks[0].engine == "rapidocr-en"
    assert any(a.engine == "rapidocr-th" and "Total 100" in a.text for a in blocks[0].alternatives)
    assert any("conflicting" in r for r in reviews)


def test_en_retry_runs_on_uncertain_thai_looking_region():
    """Handwriting misread as Thai salad (low conf) must still get an EN attempt."""
    from app.services.hybrid_ocr import hybrid_ocr_page

    rapid = _mock_rapid(["ไก่ AWMER"], [0.3])
    rapid.recognize_crops.side_effect = lambda crops, lang="en": (["AWMER"], [0.6])
    trocr = MagicMock()
    trocr.transcribe_crops.return_value = ["AWMER"]
    trocr.model_revision_info.return_value = {}
    blocks, engines, reviews, _ = hybrid_ocr_page(
        b"img", settings=_settings(), rapid_client=rapid, trocr_client=trocr)
    assert rapid.recognize_crops.called
    assert blocks and blocks[0].text == "AWMER"
    assert blocks[0].engine == "rapidocr-en"
    assert any(a.engine == "rapidocr-th" for a in blocks[0].alternatives)


def test_en_retry_skipped_for_confident_thai_print():
    """Confident Thai print keeps TH without spending an EN retry."""
    from app.services.hybrid_ocr import hybrid_ocr_page

    rapid = _mock_rapid(["ใบกำกับภาษี"], [0.95])
    hybrid_ocr_page(b"img", settings=_settings(), rapid_client=rapid, trocr_client=MagicMock())
    rapid.recognize_crops.assert_not_called()


def test_trocr_provisional_use_and_confidence_preserved():
    from app.services.hybrid_ocr import hybrid_ocr_page

    rapid = _mock_rapid(["Tetal 100"], [0.5])
    rapid.recognize_crops.side_effect = lambda crops, lang="en": (["Tetal 100"], [0.5])
    trocr = MagicMock()
    trocr.transcribe_crops.return_value = ["Total 100"]
    trocr.model_revision_info.return_value = {"trocr_model": "m", "trocr_revision": "r"}
    blocks, engines, reviews, _ = hybrid_ocr_page(
        b"img", settings=_settings(), rapid_client=rapid, trocr_client=trocr)
    assert blocks and blocks[0].text == "Total 100"
    assert blocks[0].engine == "trocr"
    # Original RapidOCR confidence kept; TrOCR alt has no confidence.
    assert blocks[0].confidence == pytest.approx(0.5)
    assert any(a.engine == "trocr" and a.confidence is None for a in blocks[0].alternatives)
    assert any("provisional" in (r or "") or "TrOCR" in r for r in reviews for r in [blocks[0].review_reason])
    assert any("trocr" in e for e in engines)


def test_trocr_failure_preserves_rapidocr():
    from app.services.hybrid_ocr import hybrid_ocr_page

    rapid = _mock_rapid(["Tetal 100"], [0.4])
    rapid.recognize_crops.side_effect = lambda crops, lang="en": (["Tetal 100"], [0.4])
    trocr = MagicMock()
    trocr.transcribe_crops.side_effect = RuntimeError("torch missing")
    trocr.model_revision_info.return_value = {}
    blocks, _, reviews, _ = hybrid_ocr_page(
        b"img", settings=_settings(), rapid_client=rapid, trocr_client=trocr)
    assert blocks and blocks[0].text == "Tetal 100"
    assert blocks[0].engine in ("rapidocr-th", "rapidocr-en")
    assert blocks[0].review_reason and "trocr" in blocks[0].review_reason.lower()


def test_trocr_limit_lowest_confidence_first():
    from app.services.hybrid_ocr import hybrid_ocr_page

    n = 15
    rapid = _mock_rapid([f"Item {i}" for i in range(n)], [0.1 + i * 0.01 for i in range(n)])
    rapid.recognize_crops.side_effect = lambda crops, lang="en": (["x"], [0.2])
    calls: list[str] = []
    trocr = MagicMock()
    trocr.model_revision_info.return_value = {}

    def _transcribe(crops):
        calls.append("one")
        return ["y"]

    trocr.transcribe_crops.side_effect = _transcribe
    hybrid_ocr_page(b"img", settings=_settings(max_regions=3), rapid_client=rapid, trocr_client=trocr)
    assert len(calls) == 3


def test_trocr_skips_thai_digit_ambiguous():
    from app.services.hybrid_ocr import hybrid_ocr_page

    rapid = _mock_rapid(
        ["ยอดรวม", "12345", "Total 发票", "Total 100"],
        [0.1, 0.1, 0.1, 0.9],
    )
    rapid.recognize_crops.side_effect = lambda crops, lang="en": (([""], [0.0]))
    trocr = MagicMock()
    trocr.transcribe_crops.return_value = ["CHANGED"]
    trocr.model_revision_info.return_value = {}
    blocks, _, _, _ = hybrid_ocr_page(
        b"img", settings=_settings(), rapid_client=rapid, trocr_client=trocr)
    # Only high-conf English is ineligible (conf 0.9); the other three skip
    # for script reasons — TrOCR never fires.
    trocr.transcribe_crops.assert_not_called()
    assert [b.text for b in blocks] != ["CHANGED"]


# -- cache / timeout / review ----------------------------------------------

def test_cache_fingerprint_includes_hybrid_settings():
    from app.core.config import Settings

    base = Settings()
    base.ocr_cache_enabled = False
    c1 = LocalOCRClient(base)
    fp1 = c1._hybrid_fingerprint()
    base.hybrid_trocr_conf_threshold = 0.5
    c2 = LocalOCRClient(base)
    assert c2._hybrid_fingerprint() != fp1
    assert fp1["trocr_threshold"] == 0.8 or fp1["trocr_threshold"] == 0.80


@pytest.mark.asyncio
async def test_hybrid_primary_failure_keeps_page_error():
    from app.core.config import Settings

    settings = Settings()
    settings.ocr_cache_enabled = False
    settings.ocr_engine = "hybrid"
    client = LocalOCRClient(settings)
    client._rapid.ocr_detailed = MagicMock(side_effect=RuntimeError("det missing"))
    data = io.BytesIO()
    from PIL import Image
    Image.new("RGB", (1300, 200), "white").save(data, format="PNG")
    texts = await client.aparse_file(data.getvalue(), "page.png")
    assert texts == [""]
    assert client.last_pages[0].error and "det missing" in client.last_pages[0].error
    assert client.last_pages[0].engine == "tesseract"  # default until hybrid succeeds


def test_review_propagation_sets_needs_review():
    doc = ExtractionResult(doc_type="invoice")
    page = OCRPage(
        text="Total 100", engine="hybrid", engines_used=["rapidocr-th", "trocr"],
        review_reasons=["hybrid: TrOCR provisional readings used — page needs review"],
        blocks=[OCRBlock(text="Total 100", confidence=0.5, box=(0, 0, 10, 10),
                         engine="trocr", review_reason="trocr provisional")],
    )
    out = _merge_ocr_reviews(doc, page)
    assert out.needs_review is True
    assert any(e.startswith("OCR: hybrid") for e in out.validation_errors)
    assert any("trocr" in e.lower() for e in out.validation_errors)


@pytest.mark.asyncio
async def test_temporal_process_page_merges_ocr_reviews(tmp_path, monkeypatch):
    from app.temporal import activities

    monkeypatch.setattr(activities, "_page_service", None)

    async def _fake_extract(filename, page_text, **kwargs):
        return ExtractionResult(doc_type="invoice", fields=[], validation_errors=[],
                                needs_review=False, completeness_score=1.0)

    class _FakeService:
        async def _extract_one_page(self, filename, page_text, ocr_notes=None, **kwargs):
            return await _fake_extract(filename, page_text)

    monkeypatch.setattr(activities, "_page_service", _FakeService())
    out = await activities.process_page_activity(
        "scan.png", "Total 100", ["hybrid: trocr provisional — verify"])
    assert out["needs_review"] is True
    assert any(e.startswith("OCR: hybrid") for e in out["validation_errors"])
    # Backward compat: no reviews → unchanged.
    out2 = await activities.process_page_activity("scan.png", "Total 100")
    assert out2["needs_review"] is False
