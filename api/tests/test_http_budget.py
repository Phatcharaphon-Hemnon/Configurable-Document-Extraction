"""HTTP-boundary dispatch-budget tests (mocked transport, real SDK + client).

Proves actual outbound HTTP request counts against the shared 4-attempt
budget across transport retries, format fallbacks, and corrective
generation — using httpx.MockTransport injected into a real AsyncOpenAI
client. SDK-level create() counting alone cannot establish this.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from pydantic import BaseModel

_REPO_ROOT = Path(__file__).resolve().parents[2]
_API_ROOT = Path(__file__).resolve().parents[1]
for _p in (_REPO_ROOT, _API_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.core.config import Settings  # noqa: E402
from app.services.client import Client, ClientError  # noqa: E402


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


def _make_client(script: list, monkeypatch) -> tuple[Client, list]:
    """Real Client over a scripted HTTP transport. Returns (client, requests)."""
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


@pytest.mark.anyio
async def test_sdk_retries_disabled_and_transport_used(monkeypatch):
    client, requests = _make_client([OK], monkeypatch)
    assert client._client.max_retries == 0
    result = await client.generate_structured(
        model="test-model", prompt="hello", response_schema=_BudgetSchema)
    assert result.parsed is not None and result.parsed.value == 7
    assert len(requests) == 1
    assert all(r.url.path.endswith("/chat/completions") for r in requests)


@pytest.mark.anyio
async def test_rate_limit_retries_stay_within_budget(monkeypatch):
    client, requests = _make_client([RATE, RATE, OK], monkeypatch)
    result = await client.generate_structured(
        model="test-model", prompt="hello", response_schema=_BudgetSchema)
    assert result.parsed is not None
    # Two transport retries + initial = 3 HTTP dispatches, budget is 4.
    assert len(requests) == 3
    assert client.last_attempts == 3 == len(requests)


@pytest.mark.anyio
async def test_corrective_generation_counts_one_more_dispatch(monkeypatch):
    bad = (200, _chat_body('{"name": "ok", "value": BROKEN}'))
    client, requests = _make_client([bad, OK], monkeypatch)
    result = await client.generate_structured(
        model="test-model", prompt="hello", response_schema=_BudgetSchema)
    assert result.parsed is not None and result.parsed.value == 7
    assert len(requests) == 2
    assert client.last_attempts == len(requests)


@pytest.mark.anyio
async def test_budget_cap_bounds_total_http_dispatches(monkeypatch):
    client, requests = _make_client([RATE] * 6, monkeypatch)
    with pytest.raises(ClientError):
        await client.generate_structured(
            model="test-model", prompt="hello", response_schema=_BudgetSchema)
    # Shared 4-attempt budget caps actual HTTP dispatches at exactly 4.
    assert len(requests) == 4
