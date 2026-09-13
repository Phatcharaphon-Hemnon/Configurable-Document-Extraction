"""Fast-path completed-result cache + explicit doc_type bypass (isolated storage).

Covers: full-hit bypass of an occupied processing lock with no LLM/OCR work,
partial hits (cached pages served, only missing pages queued), invalidation,
restart persistence, corruption, force-refresh, History-clear coordination,
duplicate concurrent uploads, explicit-type Router bypass (+400 helper),
output-tier memory, and timing-key accounting. No network, no credentials.
"""

from __future__ import annotations

import asyncio
import inspect
import io
import json
import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.core.config import Settings  # noqa: E402
from app.schemas.documents import ExtractedField, JudgeResult, RoutingDecision  # noqa: E402
from app.services.extraction_service import (  # noqa: E402
    DocumentExtractionService,
    UploadedFilePart,
    _count_pages,
    _render_previews,
    parse_doc_type,
)


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
    (kb / "field_catalog" / "po_fields.json").write_text(json.dumps({
        "doc_type": "purchase_order",
        "fields": [{"name": "po_number", "type": "string", "required": True}],
    }), encoding="utf-8")
    (kb / "field_catalog" / "delivery_note_fields.json").write_text(json.dumps({
        "doc_type": "delivery_note",
        "fields": [{"name": "delivery_number", "type": "string", "required": True}],
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


def _png_bytes(text: str = "Invoice No: INV-001 Total: 100", seed: int = 0) -> bytes:
    """Deterministic renderable single-page PNG (no OCR in tests)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (800 + seed, 600), "white")
    draw = ImageDraw.Draw(img)
    draw.text((40, 40 + seed), text, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


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


async def _prime(service: DocumentExtractionService, raw: bytes, name: str = "scan.png"):
    """Run one normal job; returns (primed_doc, job_id)."""
    job = service.job_store.create(filename=name, content_type="image/png", size_bytes=len(raw))
    await service.run_job(job.job_id, [UploadedFilePart(name, "image/png", raw)])
    status = service.get_batch_status(job.job_id)
    assert status is not None and status.status == "completed", status
    assert len(status.result.documents) == 1
    return status.result.documents[0], job.job_id


def _norm(doc) -> dict:
    d = doc.model_dump(mode="json")
    for key in ("id", "extracted_at", "source", "timings", "usage", "cache_metadata"):
        d.pop(key, None)
    return d


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def test_parse_doc_type_accepts_and_rejects():
    assert parse_doc_type(None) is None
    assert parse_doc_type("Invoice") == "invoice"
    assert parse_doc_type("purchase_order") == "purchase_order"
    with pytest.raises(ValueError):
        parse_doc_type("receipt")


def test_count_pages_images_and_garbage(tmp_path):
    assert _count_pages(_png_bytes(), "a.png") == 1
    assert _count_pages(b"not an image at all", "a.png") is None
    assert _count_pages(b"%PDF-1.4 truncated", "a.pdf") is None


def test_count_pages_pdf(tmp_path):
    import fitz

    doc = fitz.open()
    doc.new_page()
    doc.new_page()
    doc.new_page()
    raw = doc.tobytes()
    doc.close()
    assert _count_pages(raw, "multi.pdf") == 3


def test_render_previews_png(tmp_path):
    previews = _render_previews(_png_bytes(), "a.png", 300)
    assert previews is not None and len(previews) == 1
    assert previews[0][:8] == b"\x89PNG\r\n\x1a\n"
    assert _render_previews(b"garbage", "a.png", 300) is None


def test_route_exposes_doc_type_param():
    from app.api.routes import extract_document

    assert "doc_type" in inspect.signature(extract_document).parameters


# ------------------------------------------------------------------
# Fast path
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fast_path_full_hit_skips_lock_llm_and_ocr(tmp_path):
    service = _mock_service(tmp_path)
    raw = _png_bytes()
    primed, _ = await _prime(service, raw)
    service.router.classify.reset_mock()
    for extractor in service.extractors.values():
        extractor.extract.reset_mock()
    service.judge.evaluate.reset_mock()
    service.ocr.aparse_file.reset_mock()

    # Occupy the processing lock with a slow unrelated job: the cached
    # repeat must NOT wait behind it.
    await service._job_lock.acquire()
    try:
        job = service.job_store.create(filename="scan.png", content_type="image/png", size_bytes=len(raw))
        await asyncio.wait_for(
            service.run_job(job.job_id, [UploadedFilePart("scan.png", "image/png", raw)]),
            timeout=20,
        )
    finally:
        service._job_lock.release()

    status = service.get_batch_status(job.job_id)
    assert status is not None and status.status == "completed"
    doc = status.result.documents[0]
    # Same accepted data/evidence/review outcome (only per-submission
    # metadata differs).
    assert _norm(doc) == _norm(primed)
    # No OCR, no Router, no Extractor, no Judge work happened.
    service.ocr.aparse_file.assert_not_called()
    service.router.classify.assert_not_called()
    for extractor in service.extractors.values():
        extractor.extract.assert_not_called()
    service.judge.evaluate.assert_not_called()
    # Timing + provenance accounting.
    timings = status.result.timings
    assert timings.get("fast_path") == 1.0
    assert timings.get("queue") == 0.0
    assert timings.get("result_cache_hits") == 1.0
    assert timings.get("result_cache_misses") == 0.0
    assert timings.get("processing", -1) >= 0
    assert timings.get("result_cache_lookup_ms", -1) >= 0
    assert doc.cache_metadata is not None and doc.cache_metadata.hit_type == "full"
    assert doc.extracted_at == primed.extracted_at  # original timestamp preserved
    assert doc.cache_metadata.original_timings  # original compute cost preserved
    # Normal new submission: fresh IDs + live preview/download references.
    assert doc.id != primed.id
    assert doc.source is not None and doc.source.preview_url is not None
    assert doc.source.download_url is not None
    assert doc.source.source_id != (primed.source.source_id if primed.source else None)
    preview_path = service.sources.preview(doc.source.source_id, 1)
    assert preview_path.is_file()


@pytest.mark.asyncio
async def test_uncached_job_blocks_on_occupied_lock(tmp_path):
    """Control: without a cache hit, run_job still waits for the lock."""
    service = _mock_service(tmp_path)
    await service._job_lock.acquire()
    try:
        job = service.job_store.create(filename="new.png", content_type="image/png", size_bytes=10)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                service.run_job(job.job_id, [UploadedFilePart("new.png", "image/png", _png_bytes())]),
                timeout=0.5,
            )
    finally:
        service._job_lock.release()


@pytest.mark.asyncio
async def test_partial_hit_queues_only_missing_work(tmp_path):
    service = _mock_service(tmp_path)
    raw_a = _png_bytes("Invoice No: INV-001 Total: 100", seed=0)
    raw_b = _png_bytes("Invoice No: INV-002 Total: 200", seed=7)
    await _prime(service, raw_a, "a.png")
    for extractor in service.extractors.values():
        extractor.extract.reset_mock()
    service.router.classify.reset_mock()

    job = service.job_store.create(filename="2 files", content_type=None, size_bytes=1)
    await service.run_job(job.job_id, [
        UploadedFilePart("a.png", "image/png", raw_a),
        UploadedFilePart("b.png", "image/png", raw_b),
    ])
    status = service.get_batch_status(job.job_id)
    assert status is not None and status.status == "completed"
    assert len(status.result.documents) == 2
    # Partial hit: one extractor call total (only the missing page).
    calls = sum(e.extract.await_count for e in service.extractors.values())
    assert calls == 1
    assert status.result.timings.get("result_cache_hits") == 1.0
    assert status.result.timings.get("fast_path", 0.0) == 0.0


@pytest.mark.asyncio
async def test_invalidation_on_config_change(tmp_path):
    service = _mock_service(tmp_path)
    raw = _png_bytes()
    await _prime(service, raw)
    # Same files, new service instance with a different model: miss.
    service2 = _mock_service(tmp_path, extraction_model_name="other-model")
    service2.router.classify.reset_mock()
    job = service2.job_store.create(filename="scan.png", content_type="image/png", size_bytes=len(raw))
    await service2.run_job(job.job_id, [UploadedFilePart("scan.png", "image/png", raw)])
    service2.router.classify.assert_called()
    status = service2.get_batch_status(job.job_id)
    assert status.result.timings.get("fast_path", 0.0) == 0.0


@pytest.mark.asyncio
async def test_restart_persistence_and_stats(tmp_path):
    service = _mock_service(tmp_path)
    raw = _png_bytes()
    await _prime(service, raw)
    assert service.result_cache.stats()["manifests"] >= 1
    # Fresh process, same directories: manifest + entries survive restart.
    service2 = _mock_service(tmp_path)
    service2.router.classify.reset_mock()
    job = service2.job_store.create(filename="scan.png", content_type="image/png", size_bytes=len(raw))
    t0 = time.perf_counter()
    await service2.run_job(job.job_id, [UploadedFilePart("scan.png", "image/png", raw)])
    elapsed = time.perf_counter() - t0
    service2.router.classify.assert_not_called()
    status = service2.get_batch_status(job.job_id)
    assert status.result.timings.get("fast_path") == 1.0
    assert elapsed < 5.0  # repeated completed document: seconds, no LLM


@pytest.mark.asyncio
async def test_corrupt_entry_falls_through_and_is_removed(tmp_path):
    service = _mock_service(tmp_path)
    raw = _png_bytes()
    await _prime(service, raw)
    conn = sqlite3.connect(str(Path(service.result_cache.path)))
    try:
        with conn:
            conn.execute("UPDATE result_cache SET payload='not-json{{'")
    finally:
        conn.close()
    service.router.classify.reset_mock()
    job = service.job_store.create(filename="scan.png", content_type="image/png", size_bytes=len(raw))
    await service.run_job(job.job_id, [UploadedFilePart("scan.png", "image/png", raw)])
    service.router.classify.assert_called()  # recomputed honestly
    status = service.get_batch_status(job.job_id)
    assert status is not None and status.status == "completed"


@pytest.mark.asyncio
async def test_force_refresh_bypasses_fast_path(tmp_path):
    service = _mock_service(tmp_path)
    raw = _png_bytes()
    await _prime(service, raw)
    service.router.classify.reset_mock()
    job = service.job_store.create(filename="scan.png", content_type="image/png", size_bytes=len(raw))
    await service.run_job(job.job_id, [UploadedFilePart("scan.png", "image/png", raw)],
                          force_refresh=True)
    service.router.classify.assert_called()
    status = service.get_batch_status(job.job_id)
    assert status.result.timings.get("fast_path", 0.0) == 0.0


@pytest.mark.asyncio
async def test_clear_history_wipes_manifest_and_blocks_resurrection(tmp_path):
    service = _mock_service(tmp_path)
    raw = _png_bytes()
    await _prime(service, raw)
    counts = service.clear_history()
    assert service.result_cache.stats()["manifests"] == 0
    assert counts.get("result_cache", 0) >= 1
    service.router.classify.reset_mock()
    job = service.job_store.create(filename="scan.png", content_type="image/png", size_bytes=len(raw))
    await service.run_job(job.job_id, [UploadedFilePart("scan.png", "image/png", raw)])
    service.router.classify.assert_called()  # old results cannot reappear


@pytest.mark.asyncio
async def test_duplicate_concurrent_uploads_both_complete(tmp_path):
    service = _mock_service(tmp_path)
    raw = _png_bytes()
    primed, _ = await _prime(service, raw)
    service.router.classify.reset_mock()
    jobs = [service.job_store.create(filename="scan.png", content_type="image/png",
                                     size_bytes=len(raw)) for _ in range(2)]
    await asyncio.gather(*(
        service.run_job(j.job_id, [UploadedFilePart("scan.png", "image/png", raw)]) for j in jobs
    ))
    docs = []
    for j in jobs:
        status = service.get_batch_status(j.job_id)
        assert status is not None and status.status == "completed"
        docs.append(status.result.documents[0])
    service.router.classify.assert_not_called()
    assert _norm(docs[0]) == _norm(primed) == _norm(docs[1])
    assert docs[0].source.source_id != docs[1].source.source_id


# ------------------------------------------------------------------
# Explicit doc_type bypass
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_explicit_doc_type_bypasses_router(tmp_path):
    service = _mock_service(tmp_path)
    service.router.classify.reset_mock()
    response = await service.extract_group(
        [UploadedFilePart("scan.png", "image/png", _png_bytes())], doc_type="purchase_order")
    service.router.classify.assert_not_called()
    doc = response.documents[0]
    assert doc.doc_type == "purchase_order"
    assert doc.routing_reason == "explicit user selection (classification bypassed)"
    # Extraction validation still ran (evidence-checked fields present).
    assert [f.name for f in doc.fields] == ["invoice_number", "total_amount"]


@pytest.mark.asyncio
async def test_invalid_doc_type_rejected(tmp_path):
    service = _mock_service(tmp_path)
    with pytest.raises(ValueError):
        await service.extract_group([UploadedFilePart("a.png", "image/png", b"x")],
                                    doc_type="receipt")


# ------------------------------------------------------------------
# Output-tier memory: malformed output never marks a tier unsupported
# ------------------------------------------------------------------

@pytest.mark.anyio
async def test_malformed_output_keeps_strongest_tier():
    from unittest.mock import MagicMock

    from pydantic import BaseModel

    from app.core.config import Settings
    from app.services.client import Client

    class _S(BaseModel):
        name: str
        value: int

    settings = MagicMock(spec=Settings)
    settings.llm_api_key = "test-token"
    settings.llm_base_url = "https://ollama.com/v1"
    settings.llm_request_timeout_seconds = 90.0
    settings.disable_strict_json_schema = False
    client = Client(settings)
    before = set(Client._unsupported_tiers)
    try:
        def _resp(content: str):
            msg = MagicMock()
            msg.content = content
            choice = MagicMock()
            choice.message = msg
            resp = MagicMock()
            resp.choices = [choice]
            resp.usage = MagicMock()
            resp.usage.prompt_tokens = 10
            resp.usage.completion_tokens = 5
            resp.usage.total_tokens = 15
            resp.model_dump = MagicMock(return_value={"id": "test"})
            return resp

        calls = {"n": 0}

        async def mock_create(**kwargs):
            calls["n"] += 1
            assert kwargs["response_format"]["type"] == "json_schema"
            if calls["n"] == 1:
                return _resp("free-form prose, schema ignored (cloud-style)")
            return _resp('{"name": "ok", "value": 1}')

        client._client.chat.completions.create = mock_create
        first = await client.generate_structured(model="gpt-oss:20b", prompt="p",
                                                 response_schema=_S)
        assert first.parsed is not None and first.parsed.name == "ok"
        # Malformed output is retried in-tier; the tier is NOT remembered
        # as unsupported (only explicit 400-level rejections do that).
        assert ("https://ollama.com/v1", "gpt-oss:20b", "json_schema") not in Client._unsupported_tiers
        calls["n"] = 0
        client._client.chat.completions.create = mock_create
        second = await client.generate_structured(model="gpt-oss:20b", prompt="p",
                                                  response_schema=_S)
        assert second.parsed is not None
    finally:
        Client._unsupported_tiers.difference_update(Client._unsupported_tiers - before)
