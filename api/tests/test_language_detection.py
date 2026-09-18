"""Language mis-tag regression: English pages must not stay "th".

The Thai OCR model emits Thai-looking fragments on Latin/digit-only
content (borders, ruled lines, printed numbers misread as vowel marks).
The latin-page post-pass (`apply_latin_page_override`) overrides Thai-only
selections with Latin EN alternatives on Latin-majority pages, and the
router prompt bases the language tag on majority script, not artifacts.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.agents.router import _ROUTER_PROMPT  # noqa: E402
from app.services.hybrid_ocr import (  # noqa: E402
    apply_latin_page_override,
    is_thai_only_block,
    page_latin_fraction,
)


def test_page_latin_fraction():
    assert page_latin_fraction(["Hello World 123", "Total 100"]) == 1.0
    assert page_latin_fraction(["ไก่", "ข้าว"]) == 0.0
    assert page_latin_fraction([]) == 0.0
    assert page_latin_fraction([None, ""]) == 0.0
    assert page_latin_fraction(["123", "45.00"]) == 0.0  # digits are script-neutral
    mixed = page_latin_fraction(["Total รวม"])
    assert 0.0 < mixed < 1.0


def test_is_thai_only_block():
    assert is_thai_only_block("ไก่") is True
    assert is_thai_only_block("Total") is False
    assert is_thai_only_block("Total รวม") is False  # mixed script, not Thai-only
    assert is_thai_only_block("") is False
    assert is_thai_only_block(None) is False
    assert is_thai_only_block("123") is False
    assert is_thai_only_block("ไก่ 你好") is False  # other scripts excluded


def _state():
    """Synthetic per-region state: one Latin block + one Thai-only block."""
    return {
        "selected_texts": ["SHEET FOREST CAFE", "ไก่"],
        "selected_confs": [0.9, 0.6],
        "selected_engines": ["rapidocr-en", "rapidocr-th"],
        "en_texts": ["SHEET FOREST CAFE", "INV No 593101"],
        "en_confs": [0.9, 0.75],
        "alternatives": [[], []],
        "region_reviews": [None, None],
        "engines_used": ["rapidocr-th", "rapidocr-en"],
        "page_reviews": [],
    }


def _apply(state) -> int:
    return apply_latin_page_override(
        state["selected_texts"], state["selected_confs"], state["selected_engines"],
        state["en_texts"], state["en_confs"], state["alternatives"],
        state["region_reviews"], state["engines_used"], state["page_reviews"],
    )


def test_override_on_latin_page():
    state = _state()
    assert _apply(state) == 1
    assert state["selected_texts"][1] == "INV No 593101"
    assert state["selected_engines"][1] == "rapidocr-en"
    assert state["selected_confs"][1] == 0.75
    # TH reading retained as alternative + review reason.
    assert any(a.engine == "rapidocr-th" and "ไก่" in a.text
               for a in state["alternatives"][1])
    assert state["region_reviews"][1] is not None
    assert "latin-page-thai-override" in state["region_reviews"][1]
    assert any("latin-page-thai-override" in r for r in state["page_reviews"])
    # Latin block untouched.
    assert state["selected_texts"][0] == "SHEET FOREST CAFE"
    assert state["region_reviews"][0] is None


def test_no_override_on_thai_page():
    state = _state()
    state["en_texts"] = ["ใบเสร็จรับเงิน", "ไก่"]  # Thai-majority page
    state["selected_texts"] = ["ใบเสร็จรับเงิน", "ไก่"]
    state["selected_engines"] = ["rapidocr-th", "rapidocr-th"]
    assert _apply(state) == 0
    assert state["selected_texts"] == ["ใบเสร็จรับเงิน", "ไก่"]
    assert state["page_reviews"] == []


def test_no_override_without_en_reading():
    state = _state()
    state["en_texts"][1] = None  # confident-Thai skip / EN failure: no EN data
    assert _apply(state) == 0
    assert state["selected_texts"][1] == "ไก่"
    assert state["selected_engines"][1] == "rapidocr-th"


def test_no_override_when_already_english():
    state = _state()
    state["selected_engines"] = ["rapidocr-en", "rapidocr-en"]
    state["selected_texts"] = ["SHEET FOREST CAFE", "INV No 593101"]
    assert _apply(state) == 0


def _mock_rapid(th_texts, th_confs, en_returns):
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
    rapid.recognize_crops.side_effect = list(en_returns)
    return rapid


def _settings(threshold=0.80, max_regions=10):
    return SimpleNamespace(
        hybrid_trocr_conf_threshold=threshold,
        hybrid_trocr_max_regions=max_regions,
        hybrid_trocr_model="microsoft/trocr-base-handwritten",
        hybrid_trocr_revision="aff187bd81f8d73231cd3ed24b7857fcb10ae00e",
        hybrid_preprocess_version="hybrid-v2",
    )


def test_hybrid_page_latin_receipt_stays_english():
    """End-to-end shape: Latin receipt + low-conf Thai fragment with a Latin
    EN alternative resolves fully English (main stage already selects EN;
    the post-pass is a no-op safety net, never a regression)."""
    from app.services.hybrid_ocr import hybrid_ocr_page

    rapid = _mock_rapid(
        ["SHEET FOREST CAFE", "ไก่"], [0.9, 0.5],
        [(["SHEET FOREST CAFE"], [0.9]), (["INV No 593101"], [0.85])],
    )
    blocks, engines, reviews, _ = hybrid_ocr_page(
        b"img", settings=_settings(), rapid_client=rapid, trocr_client=MagicMock())
    texts = [b.text for b in blocks]
    assert texts == ["SHEET FOREST CAFE", "INV No 593101"]
    assert all(b.engine == "rapidocr-en" for b in blocks)
    assert page_latin_fraction(texts) > 0.70
    assert not any("latin-page-thai-override" in r for r in reviews)


def test_router_prompt_bases_language_on_majority_script_not_artifacts():
    assert "MAJORITY script" in _ROUTER_PROMPT
    assert "artifacts" in _ROUTER_PROMPT
    assert "never make" in _ROUTER_PROMPT
