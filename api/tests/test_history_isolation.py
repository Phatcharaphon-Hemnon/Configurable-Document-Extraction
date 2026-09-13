"""Regression: test/eval/OCR workflows must not touch application storage.

Snapshots the canonical database row count and the saved-sources directory,
runs the in-process workflows that tests, evaluation, and operator tooling
exercise (extraction pipeline, local OCR, Temporal page activity, mock
evaluation on a copied gold file), then asserts both are unchanged.

This is the guardrail behind the History reset: ad-hoc runs must never
recreate deleted rows or orphaned originals.
"""

from __future__ import annotations

import io
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.core.config import Settings  # noqa: E402
from app.schemas.documents import ExtractedField, JudgeResult, RoutingDecision  # noqa: E402
from app.services.extraction_service import DocumentExtractionService, UploadedFilePart  # noqa: E402

CANONICAL_DB = Path(__file__).resolve().parents[2] / "data" / "extraction.db"
CANONICAL_SOURCES = Path(__file__).resolve().parents[2] / "data" / "sources"


def _snapshot() -> tuple[int, set[str]]:
    jobs = 0
    if CANONICAL_DB.is_file():
        jobs = sqlite3.connect(str(CANONICAL_DB)).execute(
            "SELECT COUNT(*) FROM extraction_jobs").fetchone()[0]
    sources = set(p.name for p in CANONICAL_SOURCES.iterdir()) if CANONICAL_SOURCES.is_dir() else set()
    return jobs, sources


def _isolated_service(tmp_path: Path) -> DocumentExtractionService:
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
    service.router = MagicMock()
    service.router.classify = AsyncMock(
        return_value=RoutingDecision(doc_type="invoice", confidence=0.95, reason="test"))
    for extractor in service.extractors.values():
        extractor.extract = AsyncMock(return_value=(
            [ExtractedField(name="invoice_number", value="INV-9", confidence=0.95,
                            source_span="No: INV-9")], []))
    service.judge = MagicMock()
    service.judge.evaluate = AsyncMock(return_value=JudgeResult(score=0.9, issues=[], notes="ok"))
    return service


@pytest.mark.asyncio
async def test_workflows_leave_application_history_untouched(tmp_path):
    before = _snapshot()

    # 1) Full extraction pipeline on an isolated service.
    service = _isolated_service(tmp_path)
    service.ocr = MagicMock()
    service.ocr.aparse_file = AsyncMock(return_value=["No: INV-9"])
    from app.schemas.ocr import OCRPage

    service.ocr.last_pages = [OCRPage(text="No: INV-9")]
    await service.extract_group([UploadedFilePart("isolation-probe.png", "image/png", b"img")])

    # 2) Local OCR with an isolated cache directory.
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (60, 60), "white").save(buffer, format="PNG")
    ocr_settings = Settings()
    ocr_settings.ocr_cache_enabled = True
    ocr_settings.cache_path = str(tmp_path / "cache")
    ocr_settings.ocr_cache_path = str(tmp_path / "cache" / "ocr-results")
    from app.services.local_ocr import LocalOCRClient

    await LocalOCRClient(ocr_settings).aparse_file(buffer.getvalue(), "probe.png")

    # 3) Temporal page activity (always database-disabled internally).
    # Stub the pipeline itself: this step proves activity-level storage
    # isolation, not extraction quality (covered elsewhere).
    from app.schemas.documents import ExtractionResult
    from app.temporal import activities

    class _StubService:
        async def _extract_one_page(self, filename, page_text, ocr_notes=None, **kwargs):
            return ExtractionResult(doc_type="invoice", fields=[], validation_errors=[],
                                    needs_review=False, completeness_score=1.0)

    activities._page_service = _StubService()
    try:
        out = await activities.process_page_activity("probe.png", "No: INV-9")
    finally:
        activities._page_service = None
    assert out["doc_type"] == "invoice"

    # 4) Mock evaluation on a copied gold file (no LLM, fully isolated).
    real_gold = _API_DIR / "app" / "data" / "knowledge_base" / "ground_truth"
    entry = next(f for f in json.loads((real_gold / "manifest.json").read_text(encoding="utf-8"))["files"]
                 if f["filename"] == "Delivery_note2.png")
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    (gold_dir / "Delivery_note2.png").write_bytes((real_gold / "Delivery_note2.png").read_bytes())
    (gold_dir / "manifest.json").write_text(json.dumps({
        "version": 1, "annotation_method": "test", "release_subset": ["Delivery_note2.png"],
        "files": [entry],
    }), encoding="utf-8")
    sys.path.insert(0, str(_API_DIR / "scripts"))
    from run_eval import run

    code = await run(SimpleNamespace(gold_dir=gold_dir, all=False, subset=["Delivery_note2.png"],
                                     few_shot=0, mock=True, output_dir=tmp_path / "out"))
    assert code in (0, 2)

    assert _snapshot() == before
