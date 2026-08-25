"""Pipeline tests with mocked LLM clients — full Router→Extract→Validate→Judge flow."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.core.config import Settings  # noqa: E402
from app.schemas.documents import (
    ExtractedField,
    FileUploadMeta,
    JudgeResult,
    RoutingDecision,
)  # noqa: E402
from app.services.extraction_service import DocumentExtractionService, UploadedFilePart  # noqa: E402


def _settings(tmp_path: Path) -> Settings:
    kb = tmp_path / "kb"
    (kb / "field_catalog").mkdir(parents=True)
    (kb / "field_catalog" / "invoice_fields.json").write_text(json.dumps({
        "doc_type": "invoice",
        "fields": [
            {"name": "invoice_number", "type": "string", "required": True},
            {"name": "total_amount", "type": "number", "required": True},
        ],
    }), encoding="utf-8")
    s = Settings()
    s.knowledge_base_path = str(kb)
    s.llama_cloud_api_key = ""
    return s


def _make_service(tmp_path: Path, routing, extraction, judge) -> DocumentExtractionService:
    service = DocumentExtractionService(settings=_settings(tmp_path))
    service.llamaparse = MagicMock()
    service.llamaparse.aparse_file = AsyncMock(return_value=["Invoice No: INV-001 Total: 100"])
    service.router = MagicMock()
    service.router.classify = AsyncMock(return_value=routing)
    for extractor in service.extractors.values():
        extractor._client = MagicMock()
        extractor.extract = AsyncMock(return_value=extraction)
    service.judge = MagicMock()
    service.judge.evaluate = AsyncMock(return_value=judge)
    return service


def _routing():
    return RoutingDecision(doc_type="invoice", confidence=0.95, reason="invoice layout")


def _extraction():
    return (
        [
            ExtractedField(name="invoice_number", value="INV-001", confidence=0.95, source_span="Invoice No: INV-001"),
            ExtractedField(name="total_amount", value=100.0, confidence=0.9, source_span="Total: 100"),
        ],
        [],
    )


def _judge():
    return JudgeResult(score=0.9, issues=[], notes="ok")


@pytest.mark.asyncio
async def test_full_pipeline_happy_path(tmp_path):
    service = _make_service(tmp_path, _routing(), _extraction(), _judge())
    response = await service.extract_group([
        UploadedFilePart("scan.png", "image/png", b"\x89PNG fake"),
    ])

    assert response.error is None
    doc = response.documents[0]
    assert doc.doc_type == "invoice"
    assert [f.name for f in doc.fields] == ["invoice_number", "total_amount"]
    assert doc.validation_errors == []
    assert doc.needs_review is False
    assert doc.judge is not None and doc.judge.score == 0.9


@pytest.mark.asyncio
async def test_new_field_is_registered_in_catalog(tmp_path):
    extraction = (
        [
            ExtractedField(name="invoice_number", value="INV-1", confidence=0.9, source_span="No: INV-1"),
            ExtractedField(name="total_amount", value=5.0, confidence=0.9, source_span="Total: 5"),
            ExtractedField(name="loyalty_points", value="120", confidence=0.8, source_span="Points: 120"),
        ],
        ["loyalty_points"],
    )
    service = _make_service(tmp_path, _routing(), extraction, _judge())
    response = await service.extract_group([
        UploadedFilePart("scan.png", "image/png", b"img"),
    ])

    doc = response.documents[0]
    new_field = next(f for f in doc.fields if f.name == "loyalty_points")
    assert new_field.is_new_field is True

    # Catalog file now contains the new key.
    catalog_file = tmp_path / "kb" / "field_catalog" / "invoice_fields.json"
    assert "loyalty_points" in catalog_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_missing_evidence_flags_needs_review(tmp_path):
    extraction = (
        [
            ExtractedField(name="invoice_number", value="INV-9", confidence=0.9, source_span=None),
            ExtractedField(name="total_amount", value=1.0, confidence=0.9, source_span="Total: 1"),
        ],
        [],
    )
    service = _make_service(tmp_path, _routing(), extraction, _judge())
    response = await service.extract_group([
        UploadedFilePart("scan.png", "image/png", b"img"),
    ])

    doc = response.documents[0]
    assert any("no source_span" in e for e in doc.validation_errors)
    assert doc.needs_review is True


@pytest.mark.asyncio
async def test_low_judge_score_flags_needs_review(tmp_path):
    service = _make_service(tmp_path, _routing(), _extraction(), JudgeResult(score=0.3, issues=[], notes="bad"))
    response = await service.extract_group([
        UploadedFilePart("scan.png", "image/png", b"img"),
    ])
    assert response.documents[0].needs_review is True


@pytest.mark.asyncio
async def test_router_failure_returns_failed_stage(tmp_path):
    service = DocumentExtractionService(settings=_settings(tmp_path))
    service.llamaparse = MagicMock()
    service.llamaparse.aparse_file = AsyncMock(return_value=["Invoice No: INV-001 Total: 100"])
    service.router = MagicMock()
    service.router.classify = AsyncMock(side_effect=RuntimeError("LLM down"))

    response = await service.extract_group([
        UploadedFilePart("scan.png", "image/png", b"img"),
    ])
    doc = response.documents[0]
    assert doc.failed_stage == "router"
    assert doc.needs_review is True


def test_evaluate_precision_recall(tmp_path):
    service = DocumentExtractionService(settings=_settings(tmp_path))
    result = service.evaluate(
        prediction={"a": 1, "b": 2, "extra": 3},
        ground_truth={"a": 1, "b": 20},
    )
    # matched: a; FN: b; FP: b + extra
    assert result.precision == pytest.approx(1 / 3)
    assert result.recall == pytest.approx(1 / 2)
    assert result.f1 == pytest.approx(0.4)
    assert {"field": "b"} == {k: v for k, v in result.mismatches[0].items() if k == "field"} or result.mismatches


def test_request_meta_shape(tmp_path):
    meta = FileUploadMeta(filename="x.pdf", size_bytes=10)
    assert meta.filename == "x.pdf"
