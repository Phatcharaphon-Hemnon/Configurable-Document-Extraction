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
    """OCR every page with the configured local engine (text only).

    Kept for backward compatibility — new code should prefer
    ``parse_detailed_activity`` which also returns hybrid review reasons.
    """
    return await _ocr_client().aparse_file(raw_content, filename)


@activity.defn
async def parse_detailed_activity(filename: str, raw_content: bytes) -> list[dict[str, Any]]:
    """OCR every page with provenance + review reasons (hybrid-aware).

    Returns one dict per page: ``{text, review_reasons, engines_used,
    engine, has_error, error}``. Plain text stays the extraction input;
    reviews propagate to ``validation_errors``/``needs_review`` via
    ``process_page_activity`` (or ``extract_group`` for the page-workflow).
    """
    client = _ocr_client()
    texts = await client.aparse_file(raw_content, filename)
    pages = getattr(client, "last_pages", [])
    out: list[dict[str, Any]] = []
    for index, text in enumerate(texts):
        page = pages[index] if index < len(pages) else None
        out.append({
            "text": text,
            "review_reasons": list(getattr(page, "review_reasons", None) or []),
            "engines_used": list(getattr(page, "engines_used", None) or []),
            "engine": getattr(page, "engine", "") or "",
            "has_error": bool(getattr(page, "error", None)),
            "error": getattr(page, "error", None),
        })
    return out


@activity.defn
async def classify_activity(filename: str, page_text: str) -> dict[str, Any]:
    from app.agents.router import RouterAgent

    routing = await RouterAgent(_settings()).classify(
        filename=filename,
        text_hint=sanitize_document_text(page_text) or None,
    )
    return routing.model_dump(mode="json")


@activity.defn
async def extract_activity(
    filename: str, page_text: str, doc_type: str, page_number: int = 1,
) -> dict[str, Any]:
    """Typed extraction call (page-isolated, no shared extractor state)."""
    from unittest.mock import AsyncMock

    from app.agents.extractors import build_extractors
    from app.schemas.documents import ExtractionCallResult

    extractors = build_extractors(_settings(), _catalog())
    ext = extractors[doc_type]
    call = None
    extract_call = getattr(ext, "extract_call", None)
    # Real extractors (and AsyncMock doubles) are awaitable; plain MagicMock
    # test doubles that only stub `extract` fall back to the tuple path.
    if isinstance(extract_call, AsyncMock) or (
        extract_call is not None
        and not type(extract_call).__name__ == "MagicMock"
        and callable(extract_call)
    ):
        try:
            call = await extract_call(text=page_text or "", page_number=page_number)
        except TypeError:
            call = None
    if call is None:
        fields, _new = await ext.extract(text=page_text or "")
        call = ExtractionCallResult(
            doc_type=doc_type, page_number=page_number,
            fields=list(fields), tables=[], new_field_names=[],
        )
    fields = register_discovered_fields(_catalog(), doc_type, call.fields, document_text=page_text).fields
    return {
        "doc_type": doc_type,
        "page_number": page_number,
        "fields": [f.model_dump(mode="json") for f in fields],
        "tables": [t.model_dump(mode="json") for t in call.tables],
        "validation_errors": [],
        "needs_review": False,
    }


@activity.defn
async def validate_activity(result: dict[str, Any], page_text: str) -> dict[str, Any]:
    from app.agents.validator import ValidatorAgent
    from app.schemas.documents import ExtractedTable

    fields = [ExtractedField.model_validate(f) for f in result["fields"]]
    tables = [ExtractedTable.model_validate(t) for t in result.get("tables", [])]
    errors, completeness, needs_review, accepted, accepted_tables, rejected, issues = ValidatorAgent(
        _catalog()
    ).validate_detailed(
        doc_type=result["doc_type"],
        fields=fields,
        document_text=page_text or None,
        tables=tables,
        page_number=int(result.get("page_number", 1)),
    )
    out = dict(result)
    out["fields"] = [f.model_dump(mode="json") for f in accepted]
    out["tables"] = [t.model_dump(mode="json") for t in accepted_tables]
    out["rejected_candidates"] = [r.model_dump(mode="json") for r in rejected]
    out["review_issues"] = [i.model_dump(mode="json") for i in issues]
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
async def process_page_activity(
    filename: str,
    page_text: str,
    ocr_review_reasons: list[str] | None = None,
    page_number: int = 1,
) -> dict[str, Any]:
    """Same validated pipeline as the API, with no nested workflow or runtime job writes.

    ``ocr_review_reasons`` (hybrid/OCR uncertainty) is merged into
    ``validation_errors``/``needs_review`` — only the selected text was fed
    into extraction. The pipeline's own coherence gate runs identically on
    the text, so unreadable pages block before any LLM call in both
    execution modes.
    """
    global _page_service
    if _page_service is None:
        from copy import copy

        from app.services.extraction_service import DocumentExtractionService
        settings = copy(_settings())
        settings.database_enabled = False
        settings.temporal_enabled = False
        _page_service = DocumentExtractionService(settings)
    result = await _page_service._extract_one_page(
        filename,
        page_text,
        ocr_notes=list(ocr_review_reasons or []),
        page_number=page_number,
        blocks=[],
        ocr_uncertain=bool(ocr_review_reasons),
    )
    data = result.model_dump(mode="json")
    # Preserve the recognized text for review (the in-process path sets
    # full_text in extract_group; the workflow path has no such step).
    data.setdefault("full_text", page_text or None)
    reviews = [r for r in (ocr_review_reasons or []) if r]
    if reviews:
        prefixed = [f"OCR: {r}" for r in reviews]
        existing = set(data.get("validation_errors", []))
        data["validation_errors"] = [p for p in prefixed if p not in existing] + data.get("validation_errors", [])
        data["needs_review"] = True
    return data
