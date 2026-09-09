"""Temporal activities — thin wrappers around the agents.

Each activity builds its own lightweight agent instances (agents are cheap;
Temporal activities must be idempotent and restartable).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from temporalio import activity

from app.core.config import get_settings
from app.core.security import sanitize_document_text
from app.schemas.documents import ExtractedField
from app.services.field_catalog import FieldCatalog, register_discovered_fields
from app.services.knowledge_base import KnowledgeBaseRepository
from app.services.local_ocr import LocalOCRClient


def _settings():
    return get_settings()


def _catalog() -> FieldCatalog:
    return KnowledgeBaseRepository(Path(_settings().knowledge_base_path)).catalog


def _ocr_client() -> LocalOCRClient:
    return LocalOCRClient(_settings())


@activity.defn
async def parse_activity(filename: str, raw_content: bytes) -> list[str]:
    """OCR every page with the configured multilingual local engine."""
    return await _ocr_client().aparse_file(raw_content, filename)


@activity.defn
async def classify_activity(filename: str, page_text: str) -> dict[str, Any]:
    from app.agents.router import RouterAgent

    routing = await RouterAgent(_settings()).classify(
        filename=filename,
        text_hint=sanitize_document_text(page_text) or None,
    )
    return routing.model_dump(mode="json")


@activity.defn
async def extract_activity(filename: str, page_text: str, doc_type: str) -> dict[str, Any]:
    from app.agents.extractors import build_extractors

    extractors = build_extractors(_settings(), _catalog())
    fields, new_names = await extractors[doc_type].extract(text=page_text or "")
    fields = register_discovered_fields(_catalog(), doc_type, fields, document_text=page_text).fields
    return {
        "doc_type": doc_type,
        "fields": [f.model_dump(mode="json") for f in fields],
        "validation_errors": [],
        "needs_review": False,
    }


@activity.defn
async def validate_activity(result: dict[str, Any], page_text: str) -> dict[str, Any]:
    from app.agents.validator import ValidatorAgent

    fields = [ExtractedField.model_validate(f) for f in result["fields"]]
    errors, completeness, needs_review = ValidatorAgent(_catalog()).validate(
        doc_type=result["doc_type"],
        fields=fields,
        document_text=page_text or None,
    )
    out = dict(result)
    out["validation_errors"] = errors
    out["completeness_score"] = completeness
    out["needs_review"] = needs_review or bool(errors)
    return out


@activity.defn
async def judge_activity(result: dict[str, Any], page_text: str) -> dict[str, Any]:
    from app.agents.judge import JudgeAgent

    fields = [ExtractedField.model_validate(f) for f in result["fields"]]
    judge = await JudgeAgent(_settings()).evaluate(fields=fields, source_text=page_text or None)
    out = dict(result)
    out["judge"] = json.loads(judge.model_dump_json())
    out["needs_review"] = result.get("needs_review", False) or judge.score < 0.7
    return out


_page_service = None


@activity.defn
async def process_page_activity(filename: str, page_text: str) -> dict[str, Any]:
    """Same validated pipeline as the API, with no nested workflow or runtime job writes."""
    global _page_service
    if _page_service is None:
        from copy import copy

        from app.services.extraction_service import DocumentExtractionService
        settings = copy(_settings())
        settings.database_enabled = False
        settings.temporal_enabled = False
        _page_service = DocumentExtractionService(settings)
    result = await _page_service._extract_one_page(filename, page_text)
    return result.model_dump(mode="json")
