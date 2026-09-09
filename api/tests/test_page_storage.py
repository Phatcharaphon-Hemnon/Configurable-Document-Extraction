"""Regression tests for the dropped-PDF-pages bug, migration and mixed sources."""

import asyncio
import io
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from app.core.config import Settings
from app.database.migrate_storage import merge_history
from app.schemas.documents import ExtractionResult, FileExtractionResponse, FileUploadMeta
from app.services.extraction_service import DocumentExtractionService, UploadedFilePart
from app.services.job_store import SQLiteJobStore
from app.services.local_ocr import OCRPage


def result(kind, text):
    return ExtractionResult(
        doc_type=kind,
        full_text=text,
        fields=[dict(name="id", value=text, source_span=text, confidence=0.8)],
        needs_review=True,
    )


def test_sqlite_retains_all_pages_and_exact_types_after_restart(tmp_path):
    path = tmp_path / "history.db"
    store = SQLiteJobStore(path)
    job = store.create("mixed.pdf")
    docs = [result("invoice", "001"), result("purchase_order", "002"), result("delivery_note", "003")]
    payload = FileExtractionResponse(request=FileUploadMeta(filename="mixed.pdf"), documents=docs)
    store.save_result(job.job_id, payload.model_dump(mode="json"))
    restored = SQLiteJobStore(path).get(job.job_id)
    assert restored.result["documents"] == payload.model_dump(mode="json")["documents"]
    assert store.repo.get_job(job.job_id)["doc_type"] == "mixed"
    store.save_result(job.job_id, payload.model_dump(mode="json"))
    with store.db.connect() as conn:
        assert conn.execute("select count(*) from extraction_pages").fetchone()[0] == 3


def test_history_import_is_backed_up_repeatable_and_remaps_children(tmp_path):
    canonical = SQLiteJobStore(tmp_path / "main.db")
    legacy = SQLiteJobStore(tmp_path / "legacy.db")
    jobs = []
    for store in [canonical, legacy]:
        job = store.create("old.pdf")
        jobs.append(job.job_id)
        store.repo.complete_job(
            job.job_id,
            "invoice",
            "en",
            "ocr",
            1,
            False,
            [],
            [dict(name="invoice_number", value="001", confidence=0.9, source_span="001")],
            dict(score=0.9),
        )
    report = merge_history(tmp_path / "main.db", tmp_path / "legacy.db", tmp_path / "backups")
    assert report["imported"] == 1 and report["total"] == 2
    assert len(list((tmp_path / "backups").glob("*.db"))) == 2
    assert merge_history(tmp_path / "main.db", tmp_path / "legacy.db", tmp_path / "backups")["imported"] == 0
    with canonical.db.connect() as conn:
        assert conn.execute("select count(*) from extracted_fields").fetchone()[0] == 2
        assert conn.execute("select count(*) from judge_results").fetchone()[0] == 2
    assert {canonical.get(job).result["documents"][0]["fields"][0]["value"] for job in jobs} == {"001"}


@pytest.mark.asyncio
async def test_blank_page_and_mixed_types_keep_page_source_identity(tmp_path):
    settings = Settings()
    settings.knowledge_base_path = str(tmp_path / "kb")
    service = DocumentExtractionService(settings)
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")

    class OCR:
        last_pages = []

        async def aparse_file(self, *args):
            self.last_pages = [
                OCRPage(text="invoice", preview=buffer.getvalue()),
                OCRPage(error="blank", preview=buffer.getvalue()),
                OCRPage(text="po", preview=buffer.getvalue()),
            ]
            return ["invoice", "", "po"]

    service.ocr = OCR()
    service._extract_one_page = AsyncMock(side_effect=[result("invoice", "a"), result("purchase_order", "b")])
    response = await service.extract_group([UploadedFilePart("mixed.pdf", "application/pdf", b"fake")])
    assert len(response.documents) == 3
    assert [d.source.page_number for d in response.documents] == [1, 2, 3]
    assert response.documents[1].failed_stage == "ocr"
    assert len({d.source.source_id for d in response.documents}) == 1
    stored = service.get_batch_status(response.job_id)
    assert len(stored.result.documents) == 3
    assert stored.progress.completed_pages == 3
    assert all(service.sources.preview(d.source.source_id, d.source.page_number).is_file() for d in response.documents)


@pytest.mark.asyncio
async def test_bad_file_does_not_discard_other_file_results(tmp_path):
    settings = Settings()
    settings.knowledge_base_path = str(tmp_path / "kb")
    service = DocumentExtractionService(settings)
    service.ocr.aparse_file = AsyncMock(side_effect=[ValueError("corrupt"), ["invoice"]])
    service._extract_one_page = AsyncMock(return_value=result("invoice", "good"))
    response = await service.extract_group(
        [UploadedFilePart("bad.pdf", None, b"x"), UploadedFilePart("good.png", None, b"y")]
    )
    assert len(response.documents) == 1 and len(response.file_errors) == 1
    assert response.documents[0].source.filename == "good.png"


@pytest.mark.asyncio
async def test_next_job_runs_after_page_failure(tmp_path):
    settings = Settings()
    settings.database_enabled = False
    settings.knowledge_base_path = str(tmp_path / "kb")
    service = DocumentExtractionService(settings)
    service.ocr.aparse_file = AsyncMock(return_value=["page"])
    service._extract_one_page = AsyncMock(side_effect=[result("invoice", "bad"), result("purchase_order", "good")])
    jobs = [service.job_store.create() for _ in range(2)]
    await asyncio.gather(
        *(service.run_job(j.job_id, [UploadedFilePart(f"{i}.png", None, b"x")]) for i, j in enumerate(jobs))
    )
    assert all(service.job_store.get(j.job_id).status == "completed" for j in jobs)


@pytest.mark.asyncio
async def test_completed_page_is_checkpointed_before_later_page_finishes(tmp_path):
    settings = Settings()
    settings.knowledge_base_path = str(tmp_path / "kb")
    service = DocumentExtractionService(settings)
    service.ocr.aparse_file = AsyncMock(return_value=["first", "second"])
    job = service.job_store.create("two.pdf")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def extract(filename, page_text):
        if page_text == "second":
            entered.set()
            await release.wait()
        return result("invoice", page_text)

    service._extract_one_page = extract
    task = asyncio.create_task(service.extract_group([UploadedFilePart("two.pdf", None, b"x")], job.job_id))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        restored = SQLiteJobStore(settings.database_path).get(job.job_id)
        assert restored.status == "processing"
        assert restored.result["documents"][0]["full_text"] == "first"
        assert restored.progress["completed_pages"] == 1
    finally:
        release.set()
        await task
    assert len(service.get_batch_status(job.job_id).result.documents) == 2


@pytest.mark.asyncio
async def test_history_and_source_api_are_read_only_and_keep_every_page(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.api import routes

    settings = Settings()
    settings.database_enabled = True
    settings.knowledge_base_path = str(tmp_path / "kb")
    service = DocumentExtractionService(settings)
    monkeypatch.setattr(routes, "service", service)
    monkeypatch.setattr(routes, "settings", settings)
    source_id = service.sources.save("mixed.pdf", b"%PDF-original", "application/pdf")
    docs = [result("invoice", "Thai"), result("purchase_order", "English")]
    for i, doc in enumerate(docs, 1):
        doc.source = service.sources.reference(source_id, i, 2, f"preview-{i}".encode())
    job = service.job_store.create("mixed.pdf")
    payload = FileExtractionResponse(request=FileUploadMeta(filename="mixed.pdf"), documents=docs)
    service.job_store.save_result(job.job_id, payload.model_dump(mode="json"))
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        detail = await client.get(f"/api/history/{job.job_id}")
        assert detail.status_code == 200
        saved = detail.json()["result"]["documents"]
        assert [d["source"]["page_number"] for d in saved] == [1, 2]
        for i, doc in enumerate(saved, 1):
            preview = await client.get("/api" + doc["source"]["preview_url"])
            assert preview.content == f"preview-{i}".encode()
        original = await client.get(f"/api/sources/{source_id}")
        assert original.content == b"%PDF-original"
        assert "attachment" in original.headers["content-disposition"]
        assert (await client.get(f"/api/sources/{source_id}/pages/0")).status_code == 404
        assert (await client.get("/api/sources/not-a-uuid")).status_code == 422
    assert service.job_store.list_jobs()[1] == 1
