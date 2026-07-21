"""Unit tests for SCHEMA_MODE (strict vs. open) logic.

Covers:
1. Config validation (ValueError on invalid SCHEMA_MODE).
2. RouterAgent initialization validations (ValueError on strict mode + no KB, or strict mode + empty catalog).
3. strict mode: missing required catalog field -> is_valid == False and needs_review == True.
4. strict mode: router given an out-of-catalog document type -> out_of_catalog == True, alias mapped or not accepted.
5. strict mode: date/amount format violations -> severity == "error" and is_valid == False.
6. open mode: missing catalog field, format issues remain warnings/soft.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

# Ensure Backend package is in path
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.core.config import Settings
from app.agents.router import RouterAgent, RoutingDecision
from app.agents.validator import ValidatorAgent
from app.services.extraction_service import DocumentExtractionService
from app.schemas.documents import FieldDefinition, ExtractionField, ValidationResult, JudgeResult
from app.services.knowledge_base import KnowledgeBaseRepository


def test_config_validation() -> None:
    """Settings should raise ValueError if SCHEMA_MODE is not strict or open."""
    with patch.dict(os.environ, {"SCHEMA_MODE": "invalid"}):
        with pytest.raises(ValueError, match="SCHEMA_MODE must be 'strict' or 'open'"):
            Settings()

    with patch.dict(os.environ, {"SCHEMA_MODE": "STRICT "}):
        settings = Settings()
        assert settings.schema_mode == "strict"

    with patch.dict(os.environ, {"SCHEMA_MODE": "open"}):
        settings = Settings()
        assert settings.schema_mode == "open"


def test_router_init_strict_checks() -> None:
    """RouterAgent raises ValueError if strict mode is set but KB or catalog is missing/empty."""
    settings = MagicMock(spec=Settings)
    settings.github_models_token = "fake-key"
    settings.router_model_name = "mock-model"

    # Strict mode + no KB -> ValueError
    with pytest.raises(ValueError, match="SCHEMA_MODE=strict requires a knowledge base"):
        RouterAgent(settings, knowledge_base=None, schema_mode="strict")

    # Strict mode + empty catalog -> ValueError
    kb = MagicMock(spec=KnowledgeBaseRepository)
    kb.list_catalog_doc_types.return_value = []
    with pytest.raises(ValueError, match="SCHEMA_MODE=strict requires at least one field catalog"):
        RouterAgent(settings, knowledge_base=kb, schema_mode="strict")


@pytest.mark.anyio
async def test_router_strict_prompt_and_classification() -> None:
    """In strict mode, RouterAgent restricts classification to catalog doc types or applies aliases."""
    settings = MagicMock(spec=Settings)
    settings.github_models_token = "fake-key"
    settings.router_model_name = "mock-model"

    kb = MagicMock(spec=KnowledgeBaseRepository)
    kb.list_catalog_doc_types.return_value = ["invoice", "po", "delivery_note"]
    kb.get_catalog_fields.return_value = []

    router = RouterAgent(settings, knowledge_base=kb, schema_mode="strict")

    # Check strict prompt content
    prompt = router._build_strict_prompt("test.pdf", None)
    assert "classify it as exactly one of: invoice, po, delivery_note" in prompt

    # Mock Gemini client response
    from app.schemas.llm_schemas import RoutingResponseSchema

    response = MagicMock(spec=RoutingResponseSchema)
    response.doc_type = "purchase_order"  # Not in allowed, but in _STRICT_ALIASES mapping to "po"
    response.language = "en"
    response.reason = "looks like po"
    response.confidence = 0.9
    response.suggested_fields = []

    mock_res = MagicMock()
    mock_res.parsed = response

    with patch.object(router._client, "generate_structured", new_callable=AsyncMock, return_value=mock_res):
        routing_dec = await router.classify("test.pdf", text_hint="some text")
        assert routing_dec.doc_type == "po"  # Aligned to PO
        assert routing_dec.out_of_catalog is False

    # Out of catalog case
    response_unknown = MagicMock(spec=RoutingResponseSchema)
    response_unknown.doc_type = "receipt"  # Not in allowed and not in aliases
    response_unknown.language = "en"
    response_unknown.reason = "receipt"
    response_unknown.confidence = 0.8
    response_unknown.suggested_fields = []

    mock_res_unknown = MagicMock()
    mock_res_unknown.parsed = response_unknown

    with patch.object(router._client, "generate_structured", new_callable=AsyncMock, return_value=mock_res_unknown):
        routing_dec_unknown = await router.classify("test.pdf", text_hint="some text")
        assert routing_dec_unknown.doc_type == "receipt"
        assert routing_dec_unknown.out_of_catalog is True


def test_validator_strict_vs_open() -> None:
    """Validate behavior of ValidatorAgent in strict vs open mode."""
    validator = ValidatorAgent()

    catalog_fields = [
        FieldDefinition(name="invoice_number", likely_required=True),
        FieldDefinition(name="total_amount", likely_required=True),
        FieldDefinition(name="invoice_date", likely_required=False),
    ]

    suggested_fields = [
        FieldDefinition(name="invoice_number", likely_required=False),
        FieldDefinition(name="total_amount", likely_required=False),
    ]

    # Mode: open. Missing catalog-required field is fine because router didn't suggest it as likely_required.
    # Format issues are warning.
    extracted_open = {
        "invoice_number": ExtractionField(value="INV-123", confidence=1.0),
        "total_amount": ExtractionField(value=-10.0, confidence=1.0),  # format issue: negative amount
    }

    res_open = validator.validate(
        suggested_fields=suggested_fields,
        extracted_fields=extracted_open,
        additional_fields=None,
        schema_mode="open",
        catalog_fields=catalog_fields
    )
    assert res_open.is_valid is True  # Warning does not block
    assert len(res_open.issues) == 1
    assert res_open.issues[0].severity == "warning"
    assert "Amount should not be negative" in res_open.issues[0].message

    # Mode: strict. Missing catalog-required field (total_amount is required, but missing) -> error.
    # Negative amount -> error.
    extracted_strict = {
        "invoice_number": ExtractionField(value="INV-123", confidence=1.0),
        # missing total_amount!
    }

    res_strict_missing = validator.validate(
        suggested_fields=suggested_fields,
        extracted_fields=extracted_strict,
        additional_fields=None,
        schema_mode="strict",
        catalog_fields=catalog_fields
    )
    assert res_strict_missing.is_valid is False
    assert any(i.severity == "error" and "Missing required field" in i.message for i in res_strict_missing.issues)

    # Mode: strict. Format error (negative amount) -> error.
    extracted_strict_format = {
        "invoice_number": ExtractionField(value="INV-123", confidence=1.0),
        "total_amount": ExtractionField(value=-100.0, confidence=1.0),
    }
    res_strict_format = validator.validate(
        suggested_fields=suggested_fields,
        extracted_fields=extracted_strict_format,
        additional_fields=None,
        schema_mode="strict",
        catalog_fields=catalog_fields
    )
    assert res_strict_format.is_valid is False
    assert any(i.severity == "error" and "Amount should not be" in i.message for i in res_strict_format.issues)


@pytest.mark.anyio
async def test_extraction_service_integration_strict() -> None:
    """Integration checks on DocumentExtractionService routing and validation in strict mode."""
    tmp_path = Path("/home/phatcharaphon/Project/Configurable-Document-Extraction/Backend/app/data/knowledge_base")
    with patch.dict(os.environ, {"LLAMA_CLOUD_API_KEY": "fake-llama-key", "GITHUB_MODELS_TOKEN": "fake-github-key"}):
        settings = Settings()
        settings.knowledge_base_path = str(tmp_path)
        settings.schema_mode = "strict"
        service = DocumentExtractionService(settings=settings)

    assert service.settings.schema_mode == "strict"
    assert service.router.schema_mode == "strict"

    # Mock routing decision where out_of_catalog = True
    routing = RoutingDecision(
        doc_type="receipt",
        language="en",
        reason="looks like receipt",
        confidence=0.9,
        suggested_fields=[],
        out_of_catalog=True
    )

    extracted_fields = {"something": ExtractionField(value="123", confidence=1.0)}
    validation = ValidationResult(is_valid=True, completeness_score=1.0, issues=[])
    judge_res = JudgeResult(score=0.95, issues=[], notes="ok")

    with (
        patch.object(service.router, "classify", new_callable=AsyncMock, return_value=routing),
        patch.object(service.extractor, "extract", new_callable=AsyncMock, return_value=(extracted_fields, {})),
        patch.object(service.validator, "validate", return_value=validation),
        patch.object(service.judge, "evaluate", new_callable=AsyncMock, return_value=judge_res)
    ):
        result = await service._extract_one_page("receipt_01.pdf", "text")
        assert result.doc_type == "receipt"
        assert result.needs_review is True  # out_of_catalog triggers needs_review!


@pytest.mark.anyio
async def test_extract_group_async() -> None:
    """Verify that extract_group correctly awaits LlamaParseClient.aparse_file and processes results asynchronously."""
    tmp_path = Path("/home/phatcharaphon/Project/Configurable-Document-Extraction/Backend/app/data/knowledge_base")
    with patch.dict(os.environ, {"LLAMA_CLOUD_API_KEY": "fake-llama-key", "GITHUB_MODELS_TOKEN": "fake-github-key"}):
        settings = Settings()
        settings.knowledge_base_path = str(tmp_path)
        settings.schema_mode = "strict"
        service = DocumentExtractionService(settings=settings)

    from app.schemas.documents import ExtractionResult, ValidationResult
    mock_res_obj = ExtractionResult(
        validation=ValidationResult(is_valid=True, completeness_score=1.0, issues=[])
    )
    # Mock aparse_file to return two parsed pages
    with (
        patch.object(service.llamaparse, "aparse_file", new_callable=AsyncMock, return_value=["page 1 text", "page 2 text"]) as mock_aparse,
        patch.object(service, "_extract_one_page", new_callable=AsyncMock, return_value=mock_res_obj) as mock_extract
    ):
        from app.services.extraction_service import UploadedFilePart
        parts = [UploadedFilePart(filename="doc.pdf", content_type="application/pdf", raw_content=b"pdf_bytes")]
        response = await service.extract_group(parts)
        
        mock_aparse.assert_called_once_with(b"pdf_bytes", "doc.pdf")
        assert mock_extract.call_count == 2
        assert response.request.filename == "doc.pdf"
        assert len(response.documents) == 2
