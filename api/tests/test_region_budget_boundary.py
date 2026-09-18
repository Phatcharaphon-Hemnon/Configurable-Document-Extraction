"""Per-HTTP-boundary region dispatch budgets (mocked transport, no live calls).

Regression: the region-entry check (`page_dispatches + 4 > cap`) alone is
insufficient — a generation started with one remaining page attempt could
still dispatch up to four requests through transport retries, tier
fallbacks, and corrective generation. The remaining page/job budget must be
enforced at EVERY outbound HTTP boundary:

- dispatch allowed only when all applicable budgets have capacity;
- each dispatched attempt debited exactly once;
- retries / fallbacks / corrections blocked once any cap is reached;
- validated partials preserved with an explicit budget-exhausted status.

No live inference; httpx.MockTransport only; temporary storage only.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from pydantic import BaseModel

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.core.config import Settings  # noqa: E402
from app.services.client import Client, ClientError  # noqa: E402
from app.services.request_control import (  # noqa: E402
    DispatchBudget,
    DispatchBudgetExhausted,
    claim_dispatch_slot,
    dispatch_budget,
    dispatch_budget_status,
)


class _BudgetSchema(BaseModel):
    name: str
    value: int


def _chat_body(content: str) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": "test-model",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _rate_limit_body() -> dict:
    return {"error": {"message": "Rate limit reached, retry shortly",
                      "type": "rate_limit", "code": "rate_limit_exceeded"}}


def _unsupported_format_body() -> dict:
    return {"error": {"message": "Unrecognized request argument supplied: "
                                 "response_format json_schema is not supported "
                                 "for this model (unsupported parameter)",
                      "type": "invalid_request_error",
                      "code": "unsupported_parameter"}}


def _make_client(script: list, monkeypatch) -> tuple[Client, list]:
    requests: list[httpx.Request] = []
    plan = list(script)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        status, body = plan.pop(0) if plan else (200, _chat_body('{"name": "x", "value": 1}'))
        return httpx.Response(status, json=body)

    async def no_sleep(delay, **kwargs):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    settings = MagicMock(spec=Settings)
    settings.llm_api_key = "test-token"
    settings.llm_base_url = "https://llm.test.local/v1"
    settings.llm_request_timeout_seconds = 90.0
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return Client(settings, http_client=http_client), requests


OK = (200, _chat_body('{"name": "ok", "value": 7}'))
RATE = (429, _rate_limit_body())
BAD_JSON = (200, _chat_body('{"name": "ok", "value": BROKEN'))
UNSUPPORTED_TIER = (400, _unsupported_format_body())


def _set_budget(page_remaining: int, job_remaining: int,
                page_cap: int = 24, job_cap: int = 64):
    return dispatch_budget.set(DispatchBudget(
        page_remaining=page_remaining, job_remaining=job_remaining,
        page_cap=page_cap, job_cap=job_cap))


# ------------------------------------------------------------------
# Budget primitives: gate + exactly-once debit
# ------------------------------------------------------------------

def test_budget_gate_reports_binding_cap():
    token = _set_budget(page_remaining=0, job_remaining=5)
    try:
        assert dispatch_budget_status() is not None
        assert "page" in (dispatch_budget_status() or "")
        assert claim_dispatch_slot() is not None
    finally:
        dispatch_budget.reset(token)
    token = _set_budget(page_remaining=5, job_remaining=0)
    try:
        assert "job" in (dispatch_budget_status() or "")
    finally:
        dispatch_budget.reset(token)
    token = _set_budget(page_remaining=2, job_remaining=64)
    try:
        assert dispatch_budget_status() is None
        assert claim_dispatch_slot() is None
        assert claim_dispatch_slot() is None
        # Exactly-once debit: two claims consume exactly two slots.
        assert dispatch_budget_status() is not None
        assert "page" in (dispatch_budget_status() or "")
    finally:
        dispatch_budget.reset(token)


def test_no_budget_context_never_blocks():
    assert dispatch_budget_status() is None
    assert claim_dispatch_slot() is None


# ------------------------------------------------------------------
# One remaining attempt: retryable error must not trigger a 2nd request
# ------------------------------------------------------------------

@pytest.mark.anyio
async def test_one_remaining_attempt_blocks_transport_retry(monkeypatch):
    client, requests = _make_client([RATE, OK], monkeypatch)
    token = _set_budget(page_remaining=1, job_remaining=64)
    try:
        with pytest.raises(ClientError, match="[Bb]udget"):
            await client.generate_structured(
                model="test-model", prompt="hello", response_schema=_BudgetSchema)
    finally:
        dispatch_budget.reset(token)
    assert len(requests) == 1, f"retry must not dispatch, got {len(requests)}"


@pytest.mark.anyio
async def test_one_remaining_attempt_blocks_format_fallback(monkeypatch):
    """An explicit tier rejection consumes the last slot; the fallback tier
    must not dispatch a second request."""
    client, requests = _make_client([UNSUPPORTED_TIER, OK], monkeypatch)
    token = _set_budget(page_remaining=1, job_remaining=64)
    try:
        with pytest.raises(ClientError, match="[Bb]udget"):
            await client.generate_structured(
                model="test-model", prompt="hello", response_schema=_BudgetSchema)
    finally:
        dispatch_budget.reset(token)
    assert len(requests) == 1, f"fallback must not dispatch, got {len(requests)}"


@pytest.mark.anyio
async def test_one_remaining_attempt_blocks_corrective_generation(monkeypatch):
    """A format rejection (unparseable output) consumes the last slot; the
    single corrective generation must not dispatch a second request."""
    client, requests = _make_client([BAD_JSON, OK], monkeypatch)
    token = _set_budget(page_remaining=1, job_remaining=64)
    try:
        with pytest.raises(ClientError, match="[Bb]udget"):
            await client.generate_structured(
                model="test-model", prompt="hello", response_schema=_BudgetSchema)
    finally:
        dispatch_budget.reset(token)
    assert len(requests) == 1, f"correction must not dispatch, got {len(requests)}"


@pytest.mark.anyio
async def test_zero_remaining_dispatches_nothing(monkeypatch):
    client, requests = _make_client([OK], monkeypatch)
    token = _set_budget(page_remaining=0, job_remaining=64)
    try:
        with pytest.raises(ClientError, match="[Bb]udget"):
            await client.generate_structured(
                model="test-model", prompt="hello", response_schema=_BudgetSchema)
    finally:
        dispatch_budget.reset(token)
    assert requests == []


@pytest.mark.anyio
async def test_job_budget_binds_like_page_budget(monkeypatch):
    client, requests = _make_client([RATE, OK], monkeypatch)
    token = _set_budget(page_remaining=24, job_remaining=1)
    try:
        with pytest.raises(ClientError, match="[Bb]udget"):
            await client.generate_structured(
                model="test-model", prompt="hello", response_schema=_BudgetSchema)
    finally:
        dispatch_budget.reset(token)
    assert len(requests) == 1


@pytest.mark.anyio
async def test_each_dispatched_attempt_debited_exactly_once(monkeypatch):
    client, requests = _make_client([OK], monkeypatch)
    token = _set_budget(page_remaining=24, job_remaining=64)
    try:
        result = await client.generate_structured(
            model="test-model", prompt="hello", response_schema=_BudgetSchema)
        assert result.parsed is not None
        remaining = dispatch_budget.get()
        assert remaining is not None
        assert remaining.page_remaining == 23
        assert remaining.job_remaining == 63
    finally:
        dispatch_budget.reset(token)
    assert len(requests) == 1


# ------------------------------------------------------------------
# Service level: budget exhaustion preserves validated partials with an
# explicit budget-exhausted status (mocked extractors, temp storage only).
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_region_page_preserves_partials_on_budget_exhaustion(tmp_path):
    import json as _json
    from unittest.mock import AsyncMock

    from app.core.config import Settings
    from app.schemas.documents import (
        ExtractedField,
        ExtractionCallResult,
    )
    from app.schemas.ocr import OCRBlock, OCRPage
    from app.services.extraction_service import DocumentExtractionService

    kb = tmp_path / "kb"
    (kb / "field_catalog").mkdir(parents=True, exist_ok=True)
    (kb / "field_catalog" / "invoice_fields.json").write_text(_json.dumps({
        "doc_type": "invoice",
        "fields": [
            {"name": "invoice_number", "type": "string", "required": True},
            {"name": "total_amount", "type": "number", "required": True},
        ],
    }), encoding="utf-8")
    for name, req in (("po_fields", "po_number"),
                      ("delivery_note_fields", "delivery_number")):
        (kb / "field_catalog" / f"{name}.json").write_text(_json.dumps({
            "doc_type": "x",
            "fields": [{"name": req, "type": "string", "required": True}],
        }), encoding="utf-8")
    settings = Settings()
    settings.knowledge_base_path = str(kb)
    settings.database_enabled = True
    settings.database_path = str(tmp_path / "history.db")
    settings.source_storage_path = str(tmp_path / "sources")
    settings.cache_path = str(tmp_path / "cache")
    settings.result_cache_enabled = True
    settings.region_extraction_enabled = True
    service = DocumentExtractionService(settings=settings)
    service.ocr = MagicMock()

    blocks = [
        OCRBlock(text="ACME REPLAY CO", confidence=0.9,
                 box=(10, 10, 100, 12), block_id="h"),
        OCRBlock(text="Item", confidence=0.9,
                 box=(10, 60, 60, 12), block_id="h1"),
        OCRBlock(text="Qty", confidence=0.9,
                 box=(200, 60, 30, 12), block_id="h2"),
        OCRBlock(text="WIDGET-A", confidence=0.9,
                 box=(10, 80, 60, 12), block_id="r1"),
        OCRBlock(text="2", confidence=0.9,
                 box=(200, 80, 30, 12), block_id="q1"),
        OCRBlock(text="Total: 25.50", confidence=0.9,
                 box=(10, 130, 80, 12), block_id="t"),
    ]
    text = "ACME REPLAY CO\nItem Qty\nWIDGET-A 2\nTotal: 25.50"
    from app.services.regions import split_page

    split = split_page(blocks, text, 1)
    if len(split.regions) < 2:
        pytest.skip("geometry yields single region on this splitter version")

    from app.schemas.documents import JudgeResult, RoutingDecision

    service.router = MagicMock()
    service.router.classify = AsyncMock(
        return_value=RoutingDecision(doc_type="invoice", confidence=0.9, reason="t"))
    service.judge = MagicMock()
    service.judge.evaluate = AsyncMock(
        return_value=JudgeResult(score=0.9, issues=[], notes="ok"))

    async def _extract_budget_limited(text, **kwargs):
        if "WIDGET-A" in text:
            raise DispatchBudgetExhausted(
                "budget-exhausted: dispatch ceiling reached (page 24/24)")
        # Evidenced value (value present verbatim in its span) so the
        # validator keeps this completed region's partial.
        return ExtractionCallResult(
            doc_type="invoice", page_number=kwargs.get("page_number", 1),
            fields=[ExtractedField(name="invoice_number", value="ACME REPLAY CO",
                                   confidence=0.95,
                                   source_span="ACME REPLAY CO")],
            tables=[], new_field_names=[])

    for ext in service.extractors.values():
        ext.extract_call = _extract_budget_limited

    ocr_page = OCRPage(text=text, blocks=blocks)
    outcome = await service._extract_page_with_regions(
        job_id=service.job_store.create(
            filename="a.png", content_type="image/png",
            size_bytes=3).job_id,
        filename="a.png", page_text=text, ocr_page=ocr_page,
        ocr_uncertain_page=False, doc_type=None, page_index=1,
        progress={"dispatches": 0}, cache_on=False, region_cache=False,
        job_started=0.0, source_key="a.png:abc",
    )
    doc = outcome.document
    # Validated partial preserved (header region completed before exhaustion).
    assert any(f.name == "invoice_number" for f in doc.fields)
    assert doc.needs_review is True
    # Explicit budget-exhausted status (not a generic extractor error).
    blob = " ".join([str(e) for e in (doc.validation_errors or [])]
                     + [str(doc.diagnostics.get("extraction_path", ""))])
    assert "budget" in blob.lower()
    assert outcome.complete_for_cache is False


@pytest.mark.anyio
async def test_budget_exhaustion_is_not_retried_as_rate_limit(monkeypatch):
    """Exhaustion must surface immediately (no backoff sleep, no retry)."""
    sleeps: list[float] = []

    async def spy_sleep(delay, **kwargs):
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", spy_sleep)
    settings = MagicMock(spec=Settings)
    settings.llm_api_key = "test-token"
    settings.llm_base_url = "https://llm.test.local/v1"
    settings.llm_request_timeout_seconds = 90.0

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP dispatch allowed with zero budget")

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = Client(settings, http_client=http_client)
    token = _set_budget(page_remaining=0, job_remaining=0)
    try:
        with pytest.raises(ClientError):
            await client.generate_structured(
                model="test-model", prompt="hello", response_schema=_BudgetSchema)
    finally:
        dispatch_budget.reset(token)
    assert sleeps == []
    assert isinstance(DispatchBudgetExhausted("x"), Exception)
