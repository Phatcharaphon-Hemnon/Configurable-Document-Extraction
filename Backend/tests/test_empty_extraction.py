import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.agents.extractors import OpenSchemaExtractor, ExtractionContext
from app.core.config import Settings
from app.schemas.llm_schemas import ExtractionResponseSchema
from app.services.extraction_service import DocumentExtractionService
from app.schemas.documents import ValidationResult, ValidationIssue
from app.agents.router import RoutingDecision, DocumentLanguage


@pytest.mark.anyio
async def test_extractor_returns_empty_dicts_without_raising():
    """Test that OpenSchemaExtractor.extract returns ({}, {}) and does not raise
    when the LLM returns an empty fields list.
    """
    settings = Settings()
    extractor = OpenSchemaExtractor(settings)
    
    # Mock the LLM client to return an empty ExtractionResponseSchema
    mock_result = MagicMock()
    mock_result.parsed = ExtractionResponseSchema(fields=[])
    mock_result.prompt_tokens = 10
    mock_result.completion_tokens = 10
    mock_result.total_tokens = 20
    
    extractor._client = MagicMock()
    extractor._client.generate_structured = AsyncMock(return_value=mock_result)
    
    context = ExtractionContext(
        text="Some document text that the LLM finds no fields in.",
        doc_type="invoice",
    )
    
    extracted, additional = await extractor.extract(context)
    
    assert extracted == {}
    assert additional == {}



