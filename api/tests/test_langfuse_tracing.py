"""Tests for Langfuse v4 tracing (nesting, generations, scores, PII mask)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.guards.pii_detector import PIIDetector  # noqa: E402
from app.observability.langfuse import (  # noqa: E402
    LangfuseTracer,
    build_mask_function,
)
from app.services.extraction_service import _agent_usage  # noqa: E402


class FakeHandle:
    next_id = 0

    def __init__(self) -> None:
        FakeHandle.next_id += 1
        self.id = f"span-{FakeHandle.next_id}"
        self.trace_id = "trace-1"
        self.updates: list[dict] = []
        self.ends = 0
        self.scores: list[dict] = []
        self.io: dict = {}
        self.children: list[tuple[dict, FakeHandle]] = []

    def update(self, **kwargs):
        self.updates.append(kwargs)

    def end(self, **kwargs):
        self.ends += 1

    def score_trace(self, **kwargs):
        self.scores.append(kwargs)

    def set_trace_io(self, **kwargs):
        self.io.update(kwargs)

    def start_observation(self, **kwargs):
        child = FakeHandle()
        self.children.append((kwargs, child))
        return child


class FakeClient:
    def __init__(self) -> None:
        self.roots: list[dict] = []
        self.handles: list[FakeHandle] = []
        self.flushes = 0

    def start_observation(self, **kwargs):
        self.roots.append(kwargs)
        handle = FakeHandle()
        self.handles.append(handle)
        return handle

    def flush(self) -> None:
        self.flushes += 1


def _settings():
    settings = MagicMock()
    settings.langfuse_enabled = False
    settings.pii_detection_enabled = True
    settings.app_env = "test"
    return settings


def test_disabled_tracer_is_noop():
    tracer = LangfuseTracer(_settings())
    assert tracer.enabled is False
    trace = tracer.start_trace("extract-document", input_data={"a": 1})
    assert trace.id is None
    gen = trace.generation("classify-document", model="m", input_data={})
    gen.end(output={"x": 1}, usage={"input_tokens": 5})
    trace.score("completeness", 1.0)
    trace.set_io(input_data={}, output_data={})
    tracer.flush()  # must not raise


def test_nested_generations_carry_model_and_usage():
    tracer = LangfuseTracer(_settings(), client=FakeClient())
    assert tracer.enabled is True
    trace = tracer.start_trace("extract-document", input_data={"filename": "x.png"})
    assert trace.id == "trace-1"
    gen = trace.generation("classify-document", model="router-model",
                           input_data={"text_excerpt": "INV"})
    gen.end(output={"doc_type": "invoice"}, usage={"input_tokens": 10, "output_tokens": 4})
    trace.score("needs_review", 0.0)
    trace.end()
    tracer.flush()

    client = tracer._client
    assert client.flushes == 1
    assert client.handles[0].ends == 1  # root ended → exported
    assert len(client.roots) == 1
    root_kwargs, root_handle = client.roots[0], client.handles[0]
    assert root_kwargs["name"] == "extract-document"
    assert root_kwargs["as_type"] == "span"
    # Child spawned from the root handle (verified nesting mechanism).
    assert len(root_handle.children) == 1
    child_kwargs, child_handle = root_handle.children[0]
    assert child_kwargs["as_type"] == "generation"
    assert child_kwargs["model"] == "router-model"
    assert child_handle.updates[0]["usage_details"] == {"input_tokens": 10, "output_tokens": 4}
    assert root_handle.scores[0]["name"] == "needs_review"


def test_mask_redacts_pii_and_truncates():
    mask = build_mask_function(PIIDetector())
    assert mask is not None
    out = mask(data={"text": "pay with card 4111111111111111 please"})
    assert "4111111111111111" not in str(out)
    assert "REDACTED" in str(out)
    long_text = "x" * 5000
    out = mask(data={"text": long_text})
    assert len(out["text"]) < 5000 and "truncated" in out["text"]
    # Never raises, even on hostile input.
    assert mask(data=object()) is not None


def test_mask_none_detector_returns_none():
    assert build_mask_function(None) is None


def test_agent_usage_reads_last_usage():
    agent = MagicMock()
    agent._client.last_usage = {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15}
    assert _agent_usage(agent) == {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15}


def test_agent_usage_ignores_mocks_and_missing():
    assert _agent_usage(MagicMock()) is None  # MagicMock attr is not a dict
    assert _agent_usage(object()) is None
    agent = MagicMock()
    agent._client.last_usage = {"input_tokens": None, "output_tokens": 3}
    assert _agent_usage(agent) == {"output_tokens": 3}


@pytest.mark.asyncio
async def test_service_emits_nested_trace_with_scores(tmp_path):
    """End-to-end through extract_group with a fake Langfuse backend."""
    import json

    from app.core.config import Settings
    from app.schemas.documents import ExtractedField, JudgeResult, RoutingDecision
    from app.services.extraction_service import DocumentExtractionService, UploadedFilePart

    kb = tmp_path / "kb"
    (kb / "field_catalog").mkdir(parents=True)
    (kb / "field_catalog" / "invoice_fields.json").write_text(json.dumps({
        "doc_type": "invoice",
        "fields": [{"name": "invoice_number", "type": "string", "required": True}],
    }), encoding="utf-8")
    settings = Settings()
    settings.knowledge_base_path = str(kb)
    service = DocumentExtractionService(settings=settings)
    fake = FakeClient()
    service.tracer = LangfuseTracer(settings, client=fake)
    service.ocr = MagicMock()
    service.ocr.aparse_file = AsyncMock(return_value=["Invoice No: INV-1"])
    service.router = MagicMock()
    service.router.classify = AsyncMock(
        return_value=RoutingDecision(doc_type="invoice", confidence=0.95, reason="ocr"))
    for extractor in service.extractors.values():
        extractor.extract = AsyncMock(return_value=(
            [ExtractedField(name="invoice_number", value="INV-1", confidence=0.95,
                            source_span="Invoice No: INV-1")], []))
    service.judge = MagicMock()
    service.judge.evaluate = AsyncMock(return_value=JudgeResult(score=0.9, issues=[], notes="ok"))

    response = await service.extract_group([UploadedFilePart("a.png", "image/png", b"img")])
    assert response.error is None

    fake_client = service.tracer._client
    assert len(fake_client.roots) == 1
    assert fake_client.roots[0]["name"] == "extract-document"
    root_handle = fake_client.handles[0]
    names = [kwargs.get("name") for kwargs, _ in root_handle.children]
    assert "classify-document" in names
    assert "extract-fields" in names
    assert "validate-fields" in names
    # Generations carry a model; scores landed on the trace root.
    gen_models = [kwargs.get("model") for kwargs, _ in root_handle.children
                  if kwargs.get("as_type") == "generation"]
    assert gen_models and all(isinstance(m, str) and m for m in gen_models)
    scored = [s["name"] for s in root_handle.scores]
    assert "completeness" in scored and "needs_review" in scored
    assert root_handle.ends == 1  # root ended on the happy path
    assert fake_client.flushes >= 1
