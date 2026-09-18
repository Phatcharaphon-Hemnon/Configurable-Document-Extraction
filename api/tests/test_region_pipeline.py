"""Region extraction pipeline: checkpoints, resume, cancel, admission (offline).

All LLM/OCR is stubbed with geometric OCR blocks; no network, no models,
isolated tmp storage. Covers: full region flow + cache rules, partial
preservation after later failure, resume reuse + invalidation +
force-refresh, dispatch ceilings, Judge-unavailable exclusion, default-off
behavior, partial-result polling, cancel/admission/resume lifecycles,
migration/retention/clearing, compat fingerprinting, legacy compat.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.core.config import Settings  # noqa: E402
from app.schemas.documents import (  # noqa: E402
    ExtractedField,
    ExtractedTable,
    ExtractionCallResult,
    JudgeResult,
    RoutingDecision,
    TableCell,
    TableColumn,
)
from app.schemas.ocr import OCRBlock, OCRPage  # noqa: E402
from app.services.extraction_service import (  # noqa: E402
    DocumentExtractionService,
    UploadedFilePart,
)

PAGE_TEXT = "\n".join([
    "ACME REPLAY CO",
    "TAX INVOICE No: INV-R1",
    "Item Qty Price",
    "WIDGET-A 2 10.00",
    "GADGET-B 1 5.50",
    "Total: 25.50",
])


def _block(text, left, top, width=60, height=12, block_id=None):
    return OCRBlock(text=text, confidence=0.9, box=(left, top, width, height),
                    block_id=block_id)


def _geometry():
    """Header (2 lines) + 3-col table (header + 2 rows) + totals line."""
    return [
        _block("ACME REPLAY CO", 10, 10, block_id="b1"),
        _block("TAX INVOICE No: INV-R1", 10, 30, block_id="b2"),
        _block("Item", 10, 60, block_id="h1"),
        _block("Qty", 200, 60, 30, block_id="h2"),
        _block("Price", 300, 60, 50, block_id="h3"),
        _block("WIDGET-A", 10, 80, block_id="r1"),
        _block("2", 200, 80, 30, block_id="q1"),
        _block("10.00", 300, 80, 50, block_id="p1"),
        _block("GADGET-B", 10, 100, block_id="r2"),
        _block("1", 200, 100, 30, block_id="q2"),
        _block("5.50", 300, 100, 50, block_id="p2"),
        _block("Total: 25.50", 10, 130, block_id="t1"),
    ]


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
    s.result_cache_ttl_seconds = 3600.0
    s.result_cache_max_entries = 64
    s.region_extraction_enabled = True
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _field(name, value, span):
    return ExtractedField(name=name, value=value, confidence=0.95, source_span=span)


def _table():
    cols = [TableColumn(key="item", label="Item"), TableColumn(key="qty", label="Qty")]
    rows = [
        [TableCell(column="item", value="WIDGET-A", confidence=0.9, source_span="WIDGET-A"),
         TableCell(column="qty", value=2, confidence=0.9, source_span="WIDGET-A 2 10.00")],
        [TableCell(column="item", value="GADGET-B", confidence=0.9, source_span="GADGET-B"),
         TableCell(column="qty", value=1, confidence=0.9, source_span="GADGET-B 1 5.50")],
    ]
    return ExtractedTable(name="line_items", columns=cols, rows=rows)


def _mock_service(tmp_path: Path, **kw) -> DocumentExtractionService:
    service = DocumentExtractionService(settings=_settings(tmp_path, **kw))
    service.ocr = MagicMock()
    service.ocr.aparse_file = AsyncMock(return_value=[PAGE_TEXT])
    service.ocr.last_pages = [OCRPage(text=PAGE_TEXT, blocks=_geometry(), preview=b"png")]
    service.ocr.model_hashes = {}
    service.ocr._hybrid_fingerprint = MagicMock(return_value={})
    service.router = MagicMock()
    service.router.classify = AsyncMock(
        return_value=RoutingDecision(doc_type="invoice", confidence=0.95, reason="test"))
    service.judge = MagicMock()
    service.judge.evaluate = AsyncMock(return_value=JudgeResult(score=0.9, issues=[], notes="ok"))
    return service


def _region_calls(header_fields, table=None, totals_fields=()):
    """Content-routed extractor double (stable across reruns, unlike counters)."""
    header = list(header_fields)
    totals = list(totals_fields)

    async def _extract(text):
        if table is not None and "WIDGET-A" in text:
            return ExtractionCallResult(doc_type="invoice", page_number=1,
                                        fields=[], tables=[table], new_field_names=[])
        if totals and "Total:" in text:
            return ExtractionCallResult(doc_type="invoice", page_number=1,
                                        fields=list(totals), tables=[], new_field_names=[])
        return ExtractionCallResult(doc_type="invoice", page_number=1,
                                    fields=list(header), tables=[], new_field_names=[])

    return _extract


def _prime_success(service, raw=b"img"):
    header = [_field("invoice_number", "INV-R1", "TAX INVOICE No: INV-R1")]
    totals = [_field("total_amount", 25.50, "Total: 25.50")]
    extract = _region_calls(header, _table(), totals)
    for ext in service.extractors.values():
        ext.extract = AsyncMock(side_effect=extract)
    return service.extractors["invoice"].extract


@pytest.mark.asyncio
async def test_region_flow_merges_caches_and_reports_first_region(tmp_path):
    service = _mock_service(tmp_path)
    _prime_success(service)
    job = service.job_store.create(filename="a.png", content_type="image/png", size_bytes=3)
    await service.run_job(job.job_id, [UploadedFilePart("a.png", "image/png", b"img")])
    status = service.get_batch_status(job.job_id)
    assert status is not None and status.status == "completed"
    doc = status.result.documents[0]
    assert {f.name for f in doc.fields} == {"invoice_number", "total_amount"}
    assert len(doc.tables) == 1 and len(doc.tables[0].rows) == 2
    assert all(f.source_span and f.source_span in PAGE_TEXT for f in doc.fields)
    assert doc.timings.get("time_to_first_region", -1) >= 0
    assert status.result.timings.get("time_to_first_region", -1) >= 0
    # Sections published through progress; cache holds the completed page.
    assert any(s.status == "completed" for s in (status.progress.sections if status.progress else []))
    assert service.result_cache.stats()["entries"] >= 1
    # Clean extraction skips the Judge (existing skip gate, shared policy).
    assert service.judge.evaluate.await_count == 0
    assert doc.judge_status == "skipped"


@pytest.mark.asyncio
async def test_partial_preserved_after_later_region_failure(tmp_path):
    service = _mock_service(tmp_path)
    header = [_field("invoice_number", "INV-R1", "TAX INVOICE No: INV-R1")]
    totals = [_field("total_amount", 25.50, "Total: 25.50")]

    async def extract(text):
        if "WIDGET-A" in text:
            raise RuntimeError("table region blew up")
        if "Total:" in text:
            return ExtractionCallResult(doc_type="invoice", page_number=1,
                                        fields=list(totals), tables=[], new_field_names=[])
        return ExtractionCallResult(doc_type="invoice", page_number=1,
                                    fields=list(header), tables=[], new_field_names=[])

    for ext in service.extractors.values():
        ext.extract = AsyncMock(side_effect=extract)
    job = service.job_store.create(filename="a.png", content_type="image/png", size_bytes=3)
    await service.run_job(job.job_id, [UploadedFilePart("a.png", "image/png", b"img")])
    status = service.get_batch_status(job.job_id)
    doc = status.result.documents[0]
    # Completed regions preserved; failure explicit; review forced.
    assert any(f.name == "invoice_number" for f in doc.fields)
    assert doc.needs_review is True
    assert any("Region did not complete" in e or "did not complete" in e or "region" in e.lower()
               for e in doc.validation_errors)
    kinds = {s.status for s in (status.progress.sections if status.progress else [])}
    assert "completed" in kinds and "failed" in kinds
    # Partial page never enters the completed-result cache.
    assert service.result_cache.stats()["entries"] == 0


@pytest.mark.asyncio
async def test_resume_reuses_matching_checkpoints_only(tmp_path):
    """Resume reruns the SAME job: fingerprint-matching checkpoints skip LLM
    work; only the previously-failed region recomputes."""
    service = _mock_service(tmp_path)
    header = [_field("invoice_number", "INV-R1", "TAX INVOICE No: INV-R1")]
    totals = [_field("total_amount", 25.50, "Total: 25.50")]
    part = UploadedFilePart("a.png", "image/png", b"img")

    async def extract_fail_table(text):
        if "WIDGET-A" in text:
            raise RuntimeError("boom")
        if "Total:" in text:
            return ExtractionCallResult(doc_type="invoice", page_number=1,
                                        fields=list(totals), tables=[], new_field_names=[])
        return ExtractionCallResult(doc_type="invoice", page_number=1,
                                    fields=list(header), tables=[], new_field_names=[])

    for ext in service.extractors.values():
        ext.extract = AsyncMock(side_effect=extract_fail_table)
    job = service.job_store.create(filename="a.png", content_type="image/png", size_bytes=3)
    await service.run_job(job.job_id, [part])
    first_run_calls = service.extractors["invoice"].extract.await_count
    assert first_run_calls == 3
    # Page with a failed region is never presented as a completed cache hit.
    assert service.result_cache.stats()["entries"] == 0

    async def extract_fixed(text):
        if "WIDGET-A" in text:
            return ExtractionCallResult(doc_type="invoice", page_number=1,
                                        fields=[], tables=[_table()], new_field_names=[])
        if "Total:" in text:
            return ExtractionCallResult(doc_type="invoice", page_number=1,
                                        fields=list(totals), tables=[], new_field_names=[])
        return ExtractionCallResult(doc_type="invoice", page_number=1,
                                    fields=list(header), tables=[], new_field_names=[])

    for ext in service.extractors.values():
        ext.extract = AsyncMock(side_effect=extract_fixed)
    await service.run_job(job.job_id, [part])
    # Only the previously-failed table region re-ran (header+totals hits).
    assert service.extractors["invoice"].extract.await_count == 1
    status2 = service.get_batch_status(job.job_id)
    assert status2.status == "completed"
    assert {f.name for f in status2.result.documents[0].fields} == {"invoice_number", "total_amount"}
    assert service.result_cache.stats()["entries"] == 1


@pytest.mark.asyncio
async def test_model_change_invalidates_region_checkpoints(tmp_path):
    """Same job, changed model: fingerprints mismatch, so every region
    recomputes. The first run leaves one region failed (failed pages are
    never result-cached), isolating region reuse from page-cache hits."""
    service = _mock_service(tmp_path)
    header = [_field("invoice_number", "INV-R1", "TAX INVOICE No: INV-R1")]
    totals = [_field("total_amount", 25.50, "Total: 25.50")]
    part = UploadedFilePart("a.png", "image/png", b"img")

    async def extract_fail_table(text):
        if "WIDGET-A" in text:
            raise RuntimeError("boom")
        if "Total:" in text:
            return ExtractionCallResult(doc_type="invoice", page_number=1,
                                        fields=list(totals), tables=[], new_field_names=[])
        return ExtractionCallResult(doc_type="invoice", page_number=1,
                                    fields=list(header), tables=[], new_field_names=[])

    async def extract_fixed(text):
        if "WIDGET-A" in text:
            return ExtractionCallResult(doc_type="invoice", page_number=1,
                                        fields=[], tables=[_table()], new_field_names=[])
        if "Total:" in text:
            return ExtractionCallResult(doc_type="invoice", page_number=1,
                                        fields=list(totals), tables=[], new_field_names=[])
        return ExtractionCallResult(doc_type="invoice", page_number=1,
                                    fields=list(header), tables=[], new_field_names=[])

    for ext in service.extractors.values():
        ext.extract = AsyncMock(side_effect=extract_fail_table)
    job = service.job_store.create(filename="a.png", content_type="image/png", size_bytes=3)
    await service.run_job(job.job_id, [part])
    extract = service.extractors["invoice"].extract
    assert extract.await_count == 3
    assert service.result_cache.stats()["entries"] == 0
    # Fixed mocks, same settings: only the failed region recomputes.
    for ext in service.extractors.values():
        ext.extract = AsyncMock(side_effect=extract_fixed)
    await service.run_job(job.job_id, [part])
    extract2 = service.extractors["invoice"].extract
    assert extract2.await_count == 1
    # Changed generation settings invalidate every checkpoint.
    service.settings.extraction_model_name = "other-model"
    await service.run_job(job.job_id, [part])
    assert extract2.await_count == 1 + 3


@pytest.mark.asyncio
async def test_force_refresh_clears_region_checkpoints(tmp_path):
    service = _mock_service(tmp_path)
    extract = _prime_success(service)
    job = service.job_store.create(filename="a.png", content_type="image/png", size_bytes=3)
    await service.run_job(job.job_id, [UploadedFilePart("a.png", "image/png", b"img")])
    assert len(service.job_store.get_regions(job.job_id)) >= 3
    job2 = service.job_store.create(filename="a.png", content_type="image/png", size_bytes=3)
    await service.run_job(job2.job_id, [UploadedFilePart("a.png", "image/png", b"img")],
                          force_refresh=True)
    assert extract.await_count == 6  # 3 + 3 recomputed


@pytest.mark.asyncio
async def test_disable_caches_skips_checkpoint_writes(tmp_path):
    service = _mock_service(tmp_path)
    _prime_success(service)
    job = service.job_store.create(filename="a.png", content_type="image/png", size_bytes=3)
    await service.run_job(job.job_id, [UploadedFilePart("a.png", "image/png", b"img")],
                          disable_caches=True)
    assert service.job_store.get_regions(job.job_id) == []
    status = service.get_batch_status(job.job_id)
    assert status.result.timings.get("result_cache_hits", 0.0) == 0.0


@pytest.mark.asyncio
async def test_dispatch_ceilings_stop_with_explicit_error(tmp_path, monkeypatch):
    import app.services.regions as regions_mod

    # Shrink the page ceiling so the test stays small; per-region attempts
    # still drive the cap Honestly (each region below burns a full budget).
    monkeypatch.setattr(regions_mod, "MAX_REGION_DISPATCHES_PER_PAGE", 8)
    service = _mock_service(tmp_path)
    items = ["WIDGET-A", "GADGET-B", "THING-C", "DOODAD-D", "GIZMO-E",
             "WIDGET-F", "GADGET-G", "THING-H", "DOODAD-I", "GIZMO-J",
             "WIDGET-K", "GADGET-L", "THING-M", "DOODAD-N"]
    rows = "\n".join(f"{name} {i % 5 + 1} {10 + i * 1.25:.2f}" for i, name in enumerate(items))
    text = f"ACME REPLAY CO\nTAX INVOICE No: INV-R1\nItem Qty Price\n{rows}\nTotal: 200.00"
    blocks = [
        OCRBlock(text="ACME REPLAY CO", confidence=0.9, box=(10, 10, 120, 12), block_id="h0"),
        OCRBlock(text="TAX INVOICE No: INV-R1", confidence=0.9, box=(10, 30, 200, 12), block_id="h1"),
        OCRBlock(text="Item", confidence=0.9, box=(10, 60, 60, 12), block_id="c0"),
        OCRBlock(text="Qty", confidence=0.9, box=(200, 60, 30, 12), block_id="c1"),
        OCRBlock(text="Price", confidence=0.9, box=(300, 60, 50, 12), block_id="c2"),
    ]
    for i, name in enumerate(items):
        y = 80 + i * 20
        blocks.append(OCRBlock(text=name, confidence=0.9, box=(10, y, 90, 12), block_id=f"i{i}"))
        blocks.append(OCRBlock(text=str(i % 5 + 1), confidence=0.9, box=(200, y, 30, 12), block_id=f"q{i}"))
        blocks.append(OCRBlock(text=f"{10 + i * 1.25:.2f}", confidence=0.9, box=(300, y, 60, 12), block_id=f"p{i}"))
    blocks.append(OCRBlock(text="Total: 200.00", confidence=0.9, box=(10, 80 + 14 * 20, 140, 12), block_id="t"))
    service.ocr.aparse_file = AsyncMock(return_value=[text])
    service.ocr.last_pages = [OCRPage(text=text, blocks=blocks, preview=b"png")]

    async def extract(text):
        service.extractors["invoice"]._client.last_attempts = 4  # simulate a full-budget region call
        return ExtractionCallResult(doc_type="invoice", page_number=1, fields=[], tables=[], new_field_names=[])

    for ext in service.extractors.values():
        ext.extract = AsyncMock(side_effect=extract)
    job = service.job_store.create(filename="big.png", content_type="image/png", size_bytes=3)
    await service.run_job(job.job_id, [UploadedFilePart("big.png", "image/png", b"img")])
    status = service.get_batch_status(job.job_id)
    details = [s.detail or "" for s in (status.progress.sections if status.progress else [])]
    assert any("dispatch ceiling" in d for d in details)
    assert any(s.status == "unresolved" for s in (status.progress.sections if status.progress else []))


@pytest.mark.asyncio
async def test_judge_unavailable_page_not_cached(tmp_path):
    """Judge runs (skip gate blocked by a low-confidence field) and fails
    → judge_status unavailable → page excluded from the cache."""
    from app.services.client import ClientError
    service = _mock_service(tmp_path)
    header = [_field("invoice_number", "INV-R1", "TAX INVOICE No: INV-R1")]
    shaky = [ExtractedField(name="total_amount", value=25.50, confidence=0.50,
                            source_span="Total: 25.50")]

    async def extract(text):
        if "WIDGET-A" in text:
            return ExtractionCallResult(doc_type="invoice", page_number=1,
                                        fields=[], tables=[_table()], new_field_names=[])
        if "Total:" in text:
            return ExtractionCallResult(doc_type="invoice", page_number=1,
                                        fields=list(shaky), tables=[], new_field_names=[])
        return ExtractionCallResult(doc_type="invoice", page_number=1,
                                    fields=list(header), tables=[], new_field_names=[])

    for ext in service.extractors.values():
        ext.extract = AsyncMock(side_effect=extract)
    service.judge.evaluate = AsyncMock(side_effect=ClientError("judge down"))
    job = service.job_store.create(filename="a.png", content_type="image/png", size_bytes=3)
    await service.run_job(job.job_id, [UploadedFilePart("a.png", "image/png", b"img")])
    status = service.get_batch_status(job.job_id)
    assert service.judge.evaluate.await_count == 1
    assert status.result.documents[0].judge_status == "unavailable"
    assert service.result_cache.stats()["entries"] == 0


@pytest.mark.asyncio
async def test_default_off_uses_full_page_path(tmp_path):
    service = _mock_service(tmp_path, region_extraction_enabled=False)
    extract = _prime_success(service)
    job = service.job_store.create(filename="a.png", content_type="image/png", size_bytes=3)
    await service.run_job(job.job_id, [UploadedFilePart("a.png", "image/png", b"img")])
    # Full-page path: extractor saw the whole page text in fewer calls.
    assert extract.await_count == 1
    assert service.job_store.get_regions(job.job_id) == []
    status = service.get_batch_status(job.job_id)
    assert not (status.progress.sections if status.progress else [])


@pytest.mark.asyncio
async def test_partial_result_reconstructed_for_polling(tmp_path):
    service = _mock_service(tmp_path)
    _prime_success(service)
    job = service.job_store.create(filename="a.png", content_type="image/png", size_bytes=3)
    await service.run_job(job.job_id, [UploadedFilePart("a.png", "image/png", b"img")])
    # Simulate mid-flight: drop final documents, keep checkpoints.
    record = service.job_store.get(job.job_id)
    assert record is not None
    service.job_store.save_result(job.job_id, {"documents": []}, status="processing")
    status = service.get_batch_status(job.job_id)
    assert status is not None and status.partial_result is not None
    assert status.partial_result.complete is False
    assert status.partial_result.needs_review is True
    assert {f.name for f in status.partial_result.fields} == {"invoice_number", "total_amount"}
    # Failed jobs keep their partials visible too.
    service.job_store.fail_job(job.job_id, "boom")
    status2 = service.get_batch_status(job.job_id)
    assert status2 is not None and status2.partial_result is not None


@pytest.mark.asyncio
async def test_cancel_marks_cancelled_and_blocks_dispatch(tmp_path):
    from app.services.provider_admission import (
        admission_state,
        automatic_dispatch_allowed,
        reset_admission,
    )
    reset_admission()
    service = _mock_service(tmp_path)
    _prime_success(service)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def extract(text):
        if "WIDGET-A" in text:
            entered.set()
            await release.wait()
        return ExtractionCallResult(doc_type="invoice", page_number=1, fields=[], tables=[], new_field_names=[])

    for ext in service.extractors.values():
        ext.extract = AsyncMock(side_effect=extract)
    job = service.job_store.create(filename="a.png", content_type="image/png", size_bytes=3)
    task = asyncio.create_task(service.run_job(job.job_id, [UploadedFilePart("a.png", "image/png", b"img")]))
    await asyncio.wait_for(entered.wait(), timeout=10)
    assert service.request_job_cancellation(job.job_id) in ("queued", "processing")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    status = service.get_batch_status(job.job_id)
    assert status.status == "cancelled"
    # Duplicate cancel is idempotent.
    assert service.request_job_cancellation(job.job_id) == "cancelled"
    # Admission is unknown: automatic dispatch refused, regions preserved.
    allowed, reason = automatic_dispatch_allowed(service.settings.llm_base_url)
    assert allowed is False and "unknown" in reason
    assert admission_state(service.settings.llm_base_url)["state"] == "unknown"
    assert len(service.job_store.get_regions(job.job_id)) >= 1
    # run_job fails fast without touching extraction while unknown.
    service.extract_group = AsyncMock()
    job2 = service.job_store.create(filename="b.png", content_type="image/png", size_bytes=3)
    try:
        await service.run_job(job2.job_id, [UploadedFilePart("b.png", "image/png", b"img")])
        service.extract_group.assert_not_awaited()
        assert service.get_batch_status(job2.job_id).status == "failed"
    finally:
        reset_admission()


@pytest.mark.asyncio
async def test_rebuild_parts_needs_stored_originals(tmp_path):
    service = _mock_service(tmp_path)
    job = service.job_store.create(filename="a.png", content_type="image/png", size_bytes=3)
    with pytest.raises(LookupError):
        service.rebuild_parts_for_resume(job.job_id)
    with pytest.raises(LookupError):
        service.rebuild_parts_for_resume(UUID(int=0))
    source_id = service.sources.save("a.png", b"img-bytes", "image/png")
    service.job_store.set_progress(job.job_id, {"source_ids": [str(source_id)]})
    parts = service.rebuild_parts_for_resume(job.job_id)
    assert len(parts) == 1 and parts[0].raw_content == b"img-bytes"
    assert parts[0].filename == "a.png"


def test_region_store_migration_retention_and_clearing(tmp_path):
    from app.services.job_store import MAX_REGIONS_PER_JOB, SQLiteJobStore

    # Pre-existing DB without the regions table (legacy schema shape).
    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(legacy))
    try:
        conn.execute("CREATE TABLE extraction_jobs (id TEXT PRIMARY KEY, filename TEXT, status TEXT,"
                     " doc_type TEXT, extracted_at TIMESTAMP)")
        conn.commit()
    finally:
        conn.close()
    store = SQLiteJobStore(str(legacy))  # init migrates
    with store.db.connect() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "extraction_regions" in tables

    from uuid import uuid4
    job_id = uuid4()
    conn = sqlite3.connect(str(legacy))
    try:
        conn.execute("INSERT INTO extraction_jobs (id, filename, status) VALUES (?, ?, ?)",
                     (str(job_id), "a.png", "processing"))
        conn.commit()
    finally:
        conn.close()
    store.save_region(job_id, "r1", page_number=1, status="completed",
                      fingerprint="fp1", payload={"fields": []})
    # Restart reuse: a fresh store on the same path reads them back.
    store2 = SQLiteJobStore(str(legacy))
    regions = store2.get_regions(job_id)
    assert len(regions) == 1 and regions[0]["fingerprint"] == "fp1"
    # Retention bound evicts oldest-first.
    import app.services.job_store as job_store_mod
    old_max = job_store_mod.MAX_REGIONS_PER_JOB
    job_store_mod.MAX_REGIONS_PER_JOB = 3
    try:
        for i in range(5):
            store2.save_region(job_id, f"r{i}", status="completed",
                               fingerprint=f"fp{i}", payload={})
    finally:
        job_store_mod.MAX_REGIONS_PER_JOB = old_max
    kept = {r["region_id"] for r in store2.get_regions(job_id)}
    assert kept == {"r2", "r3", "r4"}
    # Clearing removes regions (jobs table untouched by this call).
    assert store2.clear_regions(job_id) == 3
    assert store2.get_regions(job_id) == []
    assert MAX_REGIONS_PER_JOB >= 16  # multi-region pages must fit


def test_compat_version_bump_invalidates_manifests(tmp_path, monkeypatch):
    import app.services.provider_capabilities as cap_mod
    from app.services.result_cache import ResultCache

    s = _settings(tmp_path)
    assert cap_mod.COMPAT_POLICY_VERSION.startswith("compat-v")
    before = ResultCache(s).manifest_key(file_bytes=b"d", filename="a.png", page_count=1)
    monkeypatch.setattr(cap_mod, "COMPAT_POLICY_VERSION", "compat-TEST")
    after = ResultCache(s).manifest_key(file_bytes=b"d", filename="a.png", page_count=1)
    assert before != after


def test_legacy_progress_and_status_shapes_still_validate():
    from app.schemas.documents import BatchStatusResponse
    legacy = BatchStatusResponse(
        job_id=UUID(int=1), status="completed",
        progress={"completed_pages": 1, "total_pages": 1, "stage": "completed"},
    )
    assert legacy.partial_result is None
    assert legacy.progress.sections == []


@pytest.mark.asyncio
async def test_empty_region_debits_budget_exactly_once(tmp_path, monkeypatch):
    """An empty-result region must debit its attempts once, not twice.

    Regression: the success path debited `used`, then the empty-output
    ValueError fell into the except handler which debited the same attempts
    again — one 4-attempt call consumed 8 dispatch budget and tripped the
    ceiling a region early.
    """
    import app.services.regions as regions_mod

    monkeypatch.setattr(regions_mod, "MAX_REGION_DISPATCHES_PER_PAGE", 1000)
    service = _mock_service(tmp_path)
    items = ["WIDGET-A", "GADGET-B", "THING-C", "DOODAD-D", "GIZMO-E",
             "WIDGET-F", "GADGET-G", "THING-H", "DOODAD-I", "GIZMO-J",
             "WIDGET-K", "GADGET-L", "THING-M", "DOODAD-N"]
    rows = "\n".join(f"{name} {i % 5 + 1} {10 + i * 1.25:.2f}" for i, name in enumerate(items))
    text = f"ACME REPLAY CO\nTAX INVOICE No: INV-R1\nItem Qty Price\n{rows}\nTotal: 200.00"
    blocks = [
        OCRBlock(text="ACME REPLAY CO", confidence=0.9, box=(10, 10, 120, 12), block_id="h0"),
        OCRBlock(text="TAX INVOICE No: INV-R1", confidence=0.9, box=(10, 30, 200, 12), block_id="h1"),
        OCRBlock(text="Item", confidence=0.9, box=(10, 60, 60, 12), block_id="c0"),
        OCRBlock(text="Qty", confidence=0.9, box=(200, 60, 30, 12), block_id="c1"),
        OCRBlock(text="Price", confidence=0.9, box=(300, 60, 50, 12), block_id="c2"),
    ]
    for i, name in enumerate(items):
        y = 80 + i * 20
        blocks.append(OCRBlock(text=name, confidence=0.9, box=(10, y, 90, 12), block_id=f"i{i}"))
        blocks.append(OCRBlock(text=str(i % 5 + 1), confidence=0.9, box=(200, y, 30, 12), block_id=f"q{i}"))
        blocks.append(OCRBlock(text=f"{10 + i * 1.25:.2f}", confidence=0.9, box=(300, y, 60, 12), block_id=f"p{i}"))
    blocks.append(OCRBlock(text="Total: 200.00", confidence=0.9, box=(10, 80 + 14 * 20, 140, 12), block_id="t"))
    service.ocr.aparse_file = AsyncMock(return_value=[text])
    service.ocr.last_pages = [OCRPage(text=text, blocks=blocks, preview=b"png")]

    async def extract_mixed(txt):
        service.extractors["invoice"]._client.last_attempts = 4
        if "WIDGET-A" in txt:
            # Table region: empty output (failure path) after a full budget.
            return ExtractionCallResult(doc_type="invoice", page_number=1, fields=[], tables=[], new_field_names=[])
        return ExtractionCallResult(
            doc_type="invoice", page_number=1,
            fields=[_field("invoice_number", "INV-R1", "TAX INVOICE No: INV-R1")],
            tables=[], new_field_names=[])

    for ext in service.extractors.values():
        ext.extract = AsyncMock(side_effect=extract_mixed)
    job = service.job_store.create(filename="one.png", content_type="image/png", size_bytes=3)
    await service.run_job(job.job_id, [UploadedFilePart("one.png", "image/png", b"img")])
    status = service.get_batch_status(job.job_id)
    docs = (status.result.documents if status.result else []) or []
    dispatches = [d.timings.get("region_dispatches") for d in docs
                  if d.timings and "region_dispatches" in d.timings]
    calls = sum(getattr(ext.extract, "call_count", 0) for ext in service.extractors.values())
    # Every call burned a full 4-attempt budget; debits must equal calls × 4
    # (the empty-region failure path must not debit the same attempts twice).
    assert calls >= 2, f"expected region calls, got {calls}"
    assert dispatches, "region dispatches timing missing"
    assert all(d == calls * 4 for d in dispatches), f"dispatches={dispatches} calls={calls}"
