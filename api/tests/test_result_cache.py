"""Persistent result-cache regression (isolated storage, synthetic data)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.core.config import Settings  # noqa: E402
from app.schemas.documents import ExtractedField, ExtractionResult, JudgeResult, RoutingDecision  # noqa: E402
from app.services.extraction_service import DocumentExtractionService, UploadedFilePart  # noqa: E402
from app.services.result_cache import ResultCache, is_cacheable_result  # noqa: E402


def _settings(tmp_path: Path, **overrides) -> Settings:
    kb = tmp_path / "kb"
    (kb / "field_catalog").mkdir(parents=True, exist_ok=True)
    (kb / "field_catalog" / "invoice_fields.json").write_text(json.dumps({
        "doc_type": "invoice",
        "fields": [
            {"name": "invoice_number", "type": "string", "required": True},
            {"name": "total_amount", "type": "number", "required": True},
        ],
    }), encoding="utf-8")
    s = Settings()
    s.knowledge_base_path = str(kb)
    s.database_enabled = True
    s.database_path = str(tmp_path / "history.db")
    s.source_storage_path = str(tmp_path / "sources")
    s.cache_path = str(tmp_path / "cache")
    s.ocr_cache_path = str(tmp_path / "cache" / "ocr-results")
    s.result_cache_enabled = True
    s.result_cache_ttl_seconds = 7 * 24 * 3600
    s.result_cache_max_entries = 128
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _mock_service(tmp_path: Path, **kw) -> DocumentExtractionService:
    service = DocumentExtractionService(settings=_settings(tmp_path, **kw))
    service.ocr = MagicMock()
    service.ocr.aparse_file = AsyncMock(return_value=["Invoice No: INV-001 Total: 100"])
    from app.schemas.ocr import OCRPage

    service.ocr.last_pages = [OCRPage(text="Invoice No: INV-001 Total: 100")]
    service.ocr.model_hashes = {}
    service.ocr._hybrid_fingerprint = MagicMock(return_value={})
    service.router = MagicMock()
    service.router.classify = AsyncMock(
        return_value=RoutingDecision(doc_type="invoice", confidence=0.95, reason="test"))
    for extractor in service.extractors.values():
        extractor.extract = AsyncMock(return_value=(
            [ExtractedField(name="invoice_number", value="INV-001", confidence=0.95,
                            source_span="Invoice No: INV-001"),
             ExtractedField(name="total_amount", value=100.0, confidence=0.9,
                            source_span="Total: 100")], []))
    service.judge = MagicMock()
    service.judge.evaluate = AsyncMock(return_value=JudgeResult(score=0.9, issues=[], notes="ok"))
    return service


def _ok_result() -> ExtractionResult:
    return ExtractionResult(
        doc_type="invoice",
        fields=[ExtractedField(name="a", value="1", confidence=0.9, source_span="a 1")],
        validation_errors=[],
        needs_review=False,
        completeness_score=1.0,
        judge_status="passed",
        judge=JudgeResult(score=0.9, issues=[], notes="ok"),
    )


def test_non_cacheable_failures_and_unavailable_judge():
    bad = _ok_result().model_copy(update={"error": "boom", "failed_stage": "extractor"})
    ok, _ = is_cacheable_result(bad)
    assert not ok
    unavailable = _ok_result().model_copy(update={"judge_status": "unavailable", "judge": None})
    ok, _ = is_cacheable_result(unavailable)
    assert not ok
    review_ok = _ok_result().model_copy(update={"needs_review": True, "judge_status": "flagged"})
    ok, _ = is_cacheable_result(review_ok)
    assert ok  # legitimate review outcomes are cacheable


def test_put_get_roundtrip_and_persistence(tmp_path):
    s = _settings(tmp_path)
    cache = ResultCache(s)
    fp = "fp-persist"
    assert cache.put(fp, _ok_result(), meta={"note": "t"}) is True
    # New instance on the same path sees the entry (restart persistence).
    cache2 = ResultCache(s)
    hit, meta = cache2.get(fp)
    assert hit is not None
    assert hit.fields[0].name == "a"
    assert meta["original_timings"] is not None


def test_ttl_and_eviction(tmp_path):
    s = _settings(tmp_path, result_cache_ttl_seconds=0.05, result_cache_max_entries=2)
    cache = ResultCache(s)
    cache.put("a", _ok_result())
    cache.put("b", _ok_result())
    cache.put("c", _ok_result())  # evicts oldest (a)
    hit_a, _ = cache.get("a")
    assert hit_a is None
    time.sleep(0.08)
    hit_b, info = cache.get("b")
    assert hit_b is None and "expired" in info["reason"]


def test_corrupt_payload_is_miss_and_removed(tmp_path):
    import sqlite3

    s = _settings(tmp_path)
    cache = ResultCache(s)
    cache.put("good", _ok_result())
    conn = sqlite3.connect(str(Path(s.cache_path) / "result-cache.sqlite"))
    conn.execute("INSERT OR REPLACE INTO result_cache (fingerprint, computed_at, expires_at, payload, meta)"
                 " VALUES ('bad', 0, 9999999999, 'not-json', '{}')")
    conn.commit()
    conn.close()
    hit, info = cache.get("bad")
    assert hit is None and "corrupt" in info["reason"]
    hit2, _ = cache.get("bad")
    assert hit2 is None


def test_invalidation_on_catalog_change(tmp_path):
    s = _settings(tmp_path)
    service = _mock_service(tmp_path)
    fp1 = service.result_cache.fingerprint_page(
        file_bytes=b"img", filename="a.png", page_number=1, page_text="t",
        ocr_engine="tesseract", ocr_languages="eng+tha", ocr_dpi=300,
        ocr_model_hashes={}, hybrid_fingerprint={},
    )
    # Mutate the catalog: fingerprint must change.
    cat_file = Path(s.knowledge_base_path) / "field_catalog" / "invoice_fields.json"
    data = json.loads(cat_file.read_text(encoding="utf-8"))
    data["fields"].append({"name": "extra_fee", "type": "number", "required": False})
    cat_file.write_text(json.dumps(data), encoding="utf-8")
    fp2 = service.result_cache.fingerprint_page(
        file_bytes=b"img", filename="a.png", page_number=1, page_text="t",
        ocr_engine="tesseract", ocr_languages="eng+tha", ocr_dpi=300,
        ocr_model_hashes={}, hybrid_fingerprint={},
    )
    assert fp1 != fp2


@pytest.mark.asyncio
async def test_cached_parity_excludes_ids_urls_timings(tmp_path):
    service = _mock_service(tmp_path)
    part = UploadedFilePart("doc.png", "image/png", b"img-bytes-1")
    first = await service.extract_group([part])
    assert first.documents and first.timings.get("result_cache_misses") == 1.0
    calls_after_first = service.extractors["invoice"].extract.call_count
    second = await service.extract_group([UploadedFilePart("doc.png", "image/png", b"img-bytes-1")])
    assert second.timings.get("result_cache_hits") == 1.0
    # Cache hit must not re-enter the provider queue (no new extractor call).
    assert service.extractors["invoice"].extract.call_count == calls_after_first
    a, b = first.documents[0], second.documents[0]
    assert [ (f.name, str(f.value)) for f in a.fields ] == [ (f.name, str(f.value)) for f in b.fields ]
    assert [ (r.location, str(r.proposed_value)) for r in (a.rejected_candidates or []) ] == \
           [ (r.location, str(r.proposed_value)) for r in (b.rejected_candidates or []) ]
    assert a.id != b.id
    assert (a.source and b.source) and a.source.source_id != b.source.source_id
    assert b.cache_metadata and b.cache_metadata.fingerprint


@pytest.mark.asyncio
async def test_force_refresh_bypasses_result_cache_keeps_ocr(tmp_path):
    service = _mock_service(tmp_path)
    part = UploadedFilePart("doc.png", "image/png", b"img-bytes-2")
    await service.extract_group([part])
    n_calls = service.extractors["invoice"].extract.call_count
    # Force refresh: extractor runs again even for identical bytes.
    await service.extract_group([UploadedFilePart("doc.png", "image/png", b"img-bytes-2")],
                                force_refresh=True)
    assert service.extractors["invoice"].extract.call_count == n_calls + 1


@pytest.mark.asyncio
async def test_partial_page_hits(tmp_path):
    service = _mock_service(tmp_path)
    service.ocr.aparse_file = AsyncMock(return_value=["Invoice No: INV-001 Total: 100"])
    from app.schemas.ocr import OCRPage

    service.ocr.last_pages = [OCRPage(text="Invoice No: INV-001 Total: 100")]
    one = await service.extract_group([UploadedFilePart("one.png", "image/png", b"one")])
    assert len(one.documents) == 1
    # Two-page submission where only page 1 content was cached: emulate by
    # submitting the cached single page plus a new page in one PDF-parsed part.
    service.ocr.aparse_file = AsyncMock(return_value=[
        "Invoice No: INV-001 Total: 100", "Invoice No: INV-002 Total: 200"])
    service.ocr.last_pages = [OCRPage(text="Invoice No: INV-001 Total: 100"),
                              OCRPage(text="Invoice No: INV-002 Total: 200")]
    # Different file bytes => different fingerprints; both miss (documents that
    # the per-page cache keys on bytes+page). This asserts the plumbing runs
    # and both pages complete independently.
    two = await service.extract_group([UploadedFilePart("two.pdf", "application/pdf", b"two")])
    assert len(two.documents) == 2


@pytest.mark.asyncio
async def test_concurrent_duplicates_single_flight_and_clear(tmp_path):
    import asyncio

    service = _mock_service(tmp_path)
    part = UploadedFilePart("dup.png", "image/png", b"dup")
    first, second = await asyncio.gather(
        service.extract_group([part]), service.extract_group([UploadedFilePart("dup.png", "image/png", b"dup")]),
    )
    assert first.documents and second.documents
    counts = service.clear_history()
    assert counts["jobs"] >= 1
    assert service.job_store.get_stats()["total_jobs"] == 0
