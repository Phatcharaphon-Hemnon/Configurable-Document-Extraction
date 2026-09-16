"""History clearing: bulk delete, cascade, active-job rejection, filenames.

Covers DELETE /api/history semantics without touching application storage:
every service here points at temporary databases and source directories.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.core.config import Settings  # noqa: E402
from app.schemas.documents import ExtractedField, JudgeResult, RoutingDecision  # noqa: E402
from app.services.extraction_service import DocumentExtractionService, UploadedFilePart  # noqa: E402


def _service(tmp_path: Path) -> DocumentExtractionService:
    kb = tmp_path / "kb"
    (kb / "field_catalog").mkdir(parents=True)
    (kb / "field_catalog" / "invoice_fields.json").write_text(json.dumps({
        "doc_type": "invoice",
        "fields": [{"name": "invoice_number", "type": "string", "required": True}],
    }), encoding="utf-8")
    settings = Settings()
    settings.knowledge_base_path = str(kb)
    settings.database_enabled = True
    settings.database_path = str(tmp_path / "history.db")
    settings.source_storage_path = str(tmp_path / "sources")
    service = DocumentExtractionService(settings=settings)
    from app.schemas.ocr import OCRPage

    service.ocr = MagicMock()
    service.ocr.aparse_file = AsyncMock(return_value=["No: INV-1"])
    service.ocr.last_pages = [OCRPage(text="No: INV-1")]
    service.router = MagicMock()
    service.router.classify = AsyncMock(
        return_value=RoutingDecision(doc_type="invoice", confidence=0.95, reason="test"))
    for extractor in service.extractors.values():
        extractor.extract = AsyncMock(return_value=(
            [ExtractedField(name="invoice_number", value="INV-1", confidence=0.95,
                            source_span="No: INV-1")], []))
    service.judge = MagicMock()
    service.judge.evaluate = AsyncMock(return_value=JudgeResult(score=0.9, issues=[], notes="ok"))
    return service


@pytest.mark.asyncio
async def test_bulk_clear_removes_jobs_pages_and_sources(tmp_path):
    service = _service(tmp_path)
    await service.extract_group([UploadedFilePart("real-invoice.png", "image/png", b"img")])
    assert service.job_store.get_stats()["total_jobs"] == 1
    assert any(tmp_path.joinpath("sources").iterdir())

    counts = service.clear_history()
    assert counts["jobs"] == 1
    stats = service.job_store.get_stats()
    assert stats["total_jobs"] == 0
    assert stats["by_status"] == {}
    assert stats["avg_completeness"] == 0.0
    assert list(tmp_path.joinpath("sources").iterdir()) == []


@pytest.mark.asyncio
async def test_bulk_clear_refuses_active_jobs(tmp_path):
    service = _service(tmp_path)
    job = service.job_store.create(filename="slow.pdf")
    service.job_store.mark_processing(job.job_id)
    with pytest.raises(ValueError, match="still active"):
        service.clear_history()
    service.job_store.fail_job(job.job_id, "cancelled")
    assert service.clear_history()["jobs"] == 1


@pytest.mark.asyncio
async def test_single_delete_cascades_related_rows(tmp_path):
    service = _service(tmp_path)
    response = await service.extract_group([UploadedFilePart("doc.png", "image/png", b"img")])
    import sqlite3

    con = sqlite3.connect(str(tmp_path / "history.db"))
    assert con.execute("SELECT COUNT(*) FROM extraction_pages").fetchone()[0] == 1
    assert service.job_store.repo.delete_job(response.job_id) is True
    assert con.execute("SELECT COUNT(*) FROM extraction_pages").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM extraction_jobs").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_one_job_per_submission_with_original_filenames(tmp_path):
    service = _service(tmp_path)
    service.ocr = MagicMock()
    service.ocr.aparse_file = AsyncMock(return_value=["No: INV-1", "No: INV-2"])
    from app.schemas.ocr import OCRPage

    service.ocr.last_pages = [OCRPage(text="No: INV-1"), OCRPage(text="No: INV-2")]
    response = await service.extract_group([UploadedFilePart("3492511_1.pdf", "application/pdf", b"pdf")])
    jobs, total = service.job_store.list_jobs()
    assert total == 1  # pages nest beneath one job, never separate jobs
    assert len(response.documents) == 2
    assert jobs[0]["filename"] == "3492511_1.pdf"
    # Multi-file submissions keep the batch label; previews create no jobs.
    response2 = await service.extract_group([
        UploadedFilePart("a.png", "image/png", b"img"),
        UploadedFilePart("b.png", "image/png", b"img"),
    ])
    assert response2.request.filename.startswith("2 files")
    assert service.job_store.get_stats()["total_jobs"] == 2


@pytest.mark.asyncio
async def test_no_name_based_hiding_in_history_listing(tmp_path):
    service = _service(tmp_path)
    await service.extract_group([UploadedFilePart("scan.png", "image/png", b"img")])
    jobs, total = service.job_store.list_jobs()
    assert total == 1
    assert jobs[0]["filename"] == "scan.png"  # genuine uploads stay visible


@pytest.mark.asyncio
async def test_delete_history_route(tmp_path, monkeypatch):
    from app.api import routes

    service = _service(tmp_path)
    await service.extract_group([UploadedFilePart("doc.png", "image/png", b"img")])
    monkeypatch.setattr(routes, "service", service)
    monkeypatch.setattr(routes.settings, "database_enabled", True)

    out = routes.clear_history()
    assert out["deleted"]["jobs"] == 1
    out = routes.clear_history()
    assert out["deleted"]["jobs"] == 0

    job = service.job_store.create(filename="busy.pdf")
    service.job_store.mark_processing(job.job_id)
    with pytest.raises(HTTPException) as exc_info:
        routes.clear_history()
    assert exc_info.value.status_code == 409
