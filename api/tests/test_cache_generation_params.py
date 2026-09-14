"""Cache behavior across effective generation settings (offline).

Presence of a fingerprint key is not enough: this file asserts actual
hit/miss BEHAVIOR for page entries and manifests when each
result-affecting setting changes, and hits when nothing changes.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.core.config import Settings  # noqa: E402
from app.schemas.documents import ExtractionResult  # noqa: E402
from app.services.result_cache import ResultCache  # noqa: E402

_FP_KW = dict(
    file_bytes=b"sentinel-bytes",
    filename="doc.png",
    page_number=1,
    page_text="Hello world 123",
    ocr_engine="tesseract",
    ocr_languages="eng+tha",
    ocr_dpi=300,
    ocr_model_hashes={},
    hybrid_fingerprint={},
)


def _base_settings(tmp_path: Path) -> Settings:
    s = Settings()
    s.database_enabled = False
    s.source_storage_path = str(tmp_path / "sources")
    s.cache_path = str(tmp_path / "cache")
    s.ocr_cache_path = str(tmp_path / "cache" / "ocr-results")
    s.result_cache_enabled = True
    s.result_cache_ttl_seconds = 3600.0
    s.result_cache_max_entries = 64
    return s


def _page_result() -> ExtractionResult:
    return ExtractionResult(doc_type="invoice", judge_status="passed")


# (label, settings attribute, changed value, fingerprint-kw override).
# OCR provenance is enforced from per-call arguments (not Settings), so OCR
# variants change the explicit fingerprint kwargs instead.
_VARIANTS = [
    ("llm_temperature", "llm_temperature", 0.99, {}),
    ("llm_reasoning_effort", "llm_reasoning_effort", "low", {}),
    ("extraction_max_tokens", "extraction_max_tokens", 1234, {}),
    ("router_max_tokens", "router_max_tokens", 123, {}),
    ("router_text_chars", "router_text_chars", 123, {}),
    ("disable_strict_json_schema", "disable_strict_json_schema", True, {}),
    ("few_shot_examples_per_doc_type", "few_shot_examples_per_doc_type", 2, {}),
    ("judge_skip_when_clean", "judge_skip_when_clean", False, {}),
    ("judge_skip_confidence", "judge_skip_confidence", 0.11, {}),
    ("extraction_model_name", "extraction_model_name", "other-model", {}),
    ("router_model_name", "router_model_name", "other-router", {}),
    ("judge_model_name", "judge_model_name", "other-judge", {}),
    ("llm_base_url", "llm_base_url", "https://other.example/v1", {}),
    ("ocr_engine", "ocr_engine", "rapidocr", {"ocr_engine": "rapidocr"}),
    ("ocr_languages", "ocr_languages", "eng", {"ocr_languages": "eng"}),
    ("ocr_dpi", "ocr_dpi", 150, {"ocr_dpi": 150}),
]


def _variant(base: Settings, attr: str, value) -> Settings:
    # Align the base so the ONLY difference is the variant (avoids env
    # leakage, e.g. disable_strict_json_schema defaulting True already).
    opposite = {
        "disable_strict_json_schema": False,
        "judge_skip_when_clean": True,
        "few_shot_examples_per_doc_type": 0,
    }
    s = copy.copy(base)
    if attr in opposite:
        object.__setattr__(base, attr, opposite[attr])
    setattr(s, attr, value)
    return s


@pytest.mark.parametrize("label,attr,value,fp_kw", _VARIANTS)
def test_setting_change_misses_page_and_manifest(tmp_path, label, attr, value, fp_kw):
    base = _base_settings(tmp_path)
    cache = ResultCache(base)
    fp = cache.fingerprint_page(**_FP_KW)
    assert cache.put(fp, _page_result(), meta={"probe": label})
    key = cache.manifest_key(file_bytes=b"sentinel-bytes", filename="doc.png", page_count=1)
    assert cache.manifest_put(key, 1, fp, 1, page_text="Hello world 123")

    # Unchanged settings: hits on both layers.
    hit, _meta = cache.get(fp)
    assert hit is not None
    pages, _texts, _m = cache.manifest_get(key)
    assert pages == {1: fp}

    # Changed effective setting: misses on both layers.
    other = _variant(base, attr, value)
    cache2 = ResultCache(other)
    fp_kwargs = dict(_FP_KW, **fp_kw)
    fp2 = cache2.fingerprint_page(**fp_kwargs)
    assert fp2 != fp, label
    miss, reason = cache2.get(fp2)
    assert miss is None, label
    assert reason.get("reason") == "miss", (label, reason)
    key2 = cache2.manifest_key(file_bytes=b"sentinel-bytes", filename="doc.png", page_count=1)
    assert key2 != key, label
    pages2, _t2, _m2 = cache2.manifest_get(key2)
    assert pages2 is None, label


def test_identical_settings_hit_page_and_manifest(tmp_path):
    base = _base_settings(tmp_path)
    cache = ResultCache(base)
    fp = cache.fingerprint_page(**_FP_KW)
    assert cache.put(fp, _page_result())
    key = cache.manifest_key(file_bytes=b"sentinel-bytes", filename="doc.png", page_count=1)
    assert cache.manifest_put(key, 1, fp, 1, page_text="Hello world 123")

    same = ResultCache(copy.copy(base))
    hit, _meta = same.get(cache.fingerprint_page(**_FP_KW))
    assert hit is not None
    assert hit.fields == [] and hit.judge_status == "passed"
    pages, texts, _m = same.manifest_get(
        same.manifest_key(file_bytes=b"sentinel-bytes", filename="doc.png", page_count=1))
    assert pages == {1: fp}
    assert texts.get(1) == "Hello world 123"
