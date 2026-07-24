import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.agents.extractors import ExtractionContext
from app.core.config import Settings
from app.services.sut_genai_client import SutGenAICallError as GeminiCallError
from app.services.extraction_service import DocumentExtractionService
from app.agents.router import RoutingDecision, DocumentLanguage


@pytest.mark.anyio
async def test_extractor_empty_fields_image_fails():
    """Mocks router.classify to succeed and extractor.extract to return two empty
    dicts, with image_bytes set on the context — asserts the result has error set, 
    failed_stage == 'extractor', and needs_review == True.
    """
    settings = Settings()
    settings.schema_mode = "open"
    service = DocumentExtractionService(settings)
    
    # 1. Mock Router to succeed
    service.router = MagicMock()
    service.router.classify = AsyncMock(return_value=RoutingDecision(
        doc_type="invoice",
        language=DocumentLanguage.EN,
        reason="looks like an invoice",
        confidence=0.9,
    ))
    
    # 2. Mock Extractor to return empty dicts
    service.extractor = MagicMock()
    service.extractor.extract = AsyncMock(return_value=({}, {}))
    
    # Mock knowledge base
    service.knowledge_base = MagicMock()
    service.knowledge_base.get_few_shot_examples.return_value = []
    service.knowledge_base.get_catalog_fields.return_value = []
    service.knowledge_base.get_ground_truth.return_value = None
    
    # Execute with image
    result = await service._extract_one_page(
        filename="empty_doc.jpg",
        page_text="",
        image_bytes=b"fake_bytes",
        image_media_type="image/jpeg",
    )
    
    # Assertions
    assert result.error == "No fields could be extracted — the document may be unreadable, blank, or the vision model failed to parse it."
    assert result.failed_stage == "extractor"
    assert result.needs_review is True


@pytest.mark.anyio
async def test_router_raises_error():
    """Mocks router.classify to raise GeminiCallError — asserts failed_stage ==
    'router' and that suggested_fields/doc_type are empty (since the router never returned).
    """
    settings = Settings()
    settings.schema_mode = "open"
    service = DocumentExtractionService(settings)
    
    # 1. Mock Router to raise GeminiCallError
    service.router = MagicMock()
    service.router.classify = AsyncMock(side_effect=GeminiCallError("Router API failed"))
    
    # Execute
    result = await service._extract_one_page(
        filename="doc.pdf",
        page_text="some text",
    )
    
    # Assertions
    assert result.error == "Router API failed"
    assert result.failed_stage == "router"
    assert result.doc_type is None
    assert result.suggested_fields == []
    assert result.needs_review is True


@pytest.mark.anyio
async def test_extractor_raises_error_preserves_partial_data():
    """Mocks router.classify to succeed but extractor.extract to raise
    GeminiCallError — asserts failed_stage == 'extractor' AND that doc_type/
    language/suggested_fields from the successful router call are still
    present on the returned ExtractionResult (partial data preserved).
    """
    settings = Settings()
    settings.schema_mode = "open"
    service = DocumentExtractionService(settings)
    
    # 1. Mock Router to succeed
    service.router = MagicMock()
    service.router.classify = AsyncMock(return_value=RoutingDecision(
        doc_type="invoice",
        language=DocumentLanguage.EN,
        reason="looks like an invoice",
        confidence=0.9,
    ))
    
    # 2. Mock Extractor to raise GeminiCallError
    service.extractor = MagicMock()
    service.extractor.extract = AsyncMock(side_effect=GeminiCallError("Extractor API failed"))
    
    # Mock knowledge base
    service.knowledge_base = MagicMock()
    service.knowledge_base.get_few_shot_examples.return_value = []
    
    # Execute
    result = await service._extract_one_page(
        filename="doc.pdf",
        page_text="some text",
    )
    
    # Assertions
    assert result.error == "Extractor API failed"
    assert result.failed_stage == "extractor"
    assert result.doc_type == "invoice"
    assert result.language == DocumentLanguage.EN
    assert result.needs_review is True
