"""Async job flow: explicit job ids, cancellation marking, stale cleanup."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.core.config import Settings  # noqa: E402
from app.schemas.documents import ExtractedField, JudgeResult, RoutingDecision  # noqa: E402
from app.services.extraction_service import DocumentExtractionService, UploadedFilePart  # noqa: E402
from app.services.job_store import InMemoryJobStore, SQLiteJobStore  # noqa: E402


def _settings() -> Settings:
    s = Settings()
    s.database_enabled = False  # keep tests on the in-memory store
    return s


def _service() -> DocumentExtractionService:
    service = DocumentExtractionService(settings=_settings())
    assert isinstance(service.job_store, InMemoryJobStore)
    service.ocr = MagicMock()
    service.ocr.aparse_file = AsyncMock(return_value=["Invoice No: INV-1 Total: 5"])
    service.router = MagicMock()
    service.router.classify = AsyncMock(
        return_value=RoutingDecision(doc_type="invoice", confidence=0.95, reason="ocr")
    )
    for extractor in service.extractors.values():
        extractor.extract = AsyncMock(return_value=(
            [ExtractedField(name="invoice_number", value="INV-1", confidence=0.95,
                            source_span="Invoice No: INV-1")],
            [],
        ))
    service.judge = MagicMock()
    service.judge.evaluate = AsyncMock(return_value=JudgeResult(score=0.9, issues=[], notes="ok"))
    return service


@pytest.mark.asyncio
async def test_extract_group_reuses_caller_job_id():
    service = _service()
    job = service.job_store.create(filename="a.png")
    response = await service.extract_group(
        [UploadedFilePart("a.png", "image/png", b"img")], job_id=job.job_id
    )
    assert response.job_id == str(job.job_id)
    stored = service.job_store.get(job.job_id)
    assert stored is not None and stored.status == "completed"


@pytest.mark.asyncio
async def test_cancelled_extraction_marks_job_failed():
    service = _service()
    service.ocr.aparse_file = AsyncMock(side_effect=asyncio.CancelledError())
    job = service.job_store.create(filename="b.png")
    with pytest.raises(asyncio.CancelledError):
        await service.extract_group(
            [UploadedFilePart("b.png", "image/png", b"img")], job_id=job.job_id
        )
    stored = service.job_store.get(job.job_id)
    assert stored is not None and stored.status == "failed"
    assert service.job_store.get_error(job.job_id) is not None


@pytest.mark.asyncio
async def test_run_job_swallows_unexpected_errors():
    service = _service()
    service.extract_group = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    await service.run_job(uuid4(), [UploadedFilePart("c.png", "image/png", b"img")])


def test_get_batch_status_surfaces_failed_error():
    service = _service()
    job = service.job_store.create(filename="d.png")
    service.job_store.fail_job(job.job_id, "OCR exploded")
    status = service.get_batch_status(job.job_id)
    assert status is not None and status.status == "failed"
    assert status.error == "OCR exploded"
    assert status.result is None


def test_sqlite_fail_stale_queued(tmp_path):
    store = SQLiteJobStore(db_path=str(tmp_path / "jobs.db"))
    j1 = store.create(filename="old1.png")
    j2 = store.create(filename="old2.png")
    assert store.fail_stale_queued() == 2
    assert store.get(j1.job_id).status == "failed"
    assert store.get_error(j2.job_id) == "Interrupted (server restarted or request cancelled)"
    # Completed jobs are untouched.
    j3 = store.create(filename="ok.png")
    store.save_result(j3.job_id, {"documents": [], "error": "empty"})
    assert store.fail_stale_queued() == 0


def test_service_init_cleans_stale_queued(tmp_path):
    db_path = str(tmp_path / "boot.db")
    store = SQLiteJobStore(db_path=db_path)
    store.create(filename="orphan.png")

    settings = Settings()
    settings.database_enabled = True
    settings.database_path = db_path
    kb = tmp_path / "kb"
    (kb / "field_catalog").mkdir(parents=True)
    (kb / "field_catalog" / "invoice_fields.json").write_text(
        json.dumps({"doc_type": "invoice", "fields": []}), encoding="utf-8"
    )
    settings.knowledge_base_path = str(kb)

    DocumentExtractionService(settings=settings)
    check = SQLiteJobStore(db_path=db_path)
    jobs, _ = check.list_jobs(status="failed")
    assert any(j["filename"] == "orphan.png" for j in jobs)
