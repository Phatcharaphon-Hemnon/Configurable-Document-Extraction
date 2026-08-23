"""Tests for the Temporal.io PoC — classify-document workflow.

Runs against an in-memory Temporal test environment; does NOT require a
``temporal server start-dev`` process.  Real OpenRouter calls are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.agents.router import RoutingDecision
from app.schemas.documents import DocumentLanguage
from app.temporal.activities import classify_document
from app.temporal.workflows import ClassifyDocumentWorkflow


@pytest.mark.anyio
async def test_classify_document_workflow() -> None:
    """End-to-end workflow test with mocked RouterAgent.classify."""

    # Build a fake RoutingDecision matching the shape returned by the real
    # RouterAgent.classify — copied from test_schema_mode.py / test_failed_stages.py.
    fake_decision = RoutingDecision(
        doc_type="invoice",
        language=DocumentLanguage.EN,
        reason="looks like an invoice",
        confidence=0.95,
        suggested_fields=[],
        out_of_catalog=False,
    )

    async with await WorkflowEnvironment.start_local() as env:
        # Patch RouterAgent.classify so no real LLM call is made.
        with patch.object(
            RoutingDecision.__class__,  # placeholder — actual target below
            "classify",
            new_callable=AsyncMock,
            return_value=fake_decision,
        ) if False else patch(
            "app.agents.router.RouterAgent.classify",
            new_callable=AsyncMock,
            return_value=fake_decision,
        ):
            task_queue = "test-poc-queue"

            async with Worker(
                env.client,
                task_queue=task_queue,
                workflows=[ClassifyDocumentWorkflow],
                activities=[classify_document],
            ):
                result = await env.client.execute_workflow(
                    ClassifyDocumentWorkflow.run,
                    args=["test-invoice.pdf", "INVOICE #12345 Total: $100"],
                    id="test-classify-poc-1",
                    task_queue=task_queue,
                )

    # Verify the dict returned by the workflow matches the mock.
    assert isinstance(result, dict)
    assert result["doc_type"] == "invoice"
    assert result["language"] == "en"
    assert result["reason"] == "looks like an invoice"
    assert result["confidence"] == 0.95
    assert result["suggested_fields"] == []
    assert result["out_of_catalog"] is False
