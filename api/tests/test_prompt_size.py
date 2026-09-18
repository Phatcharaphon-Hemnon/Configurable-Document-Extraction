"""Prompt-size instrumentation at the extractor transport boundary.

Chars + short sha of the outgoing prompt — never bodies or document
content. Verified via MockTransport dry-run construction only; no live
LLM call anywhere in this file.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from pydantic import BaseModel

_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from app.core.config import Settings  # noqa: E402
from app.services.client import Client  # noqa: E402
from app.services.request_control import (  # noqa: E402
    DispatchEvent,
    collect_dispatches,
    prompt_fingerprint,
    stage_context,
)


class _PingSchema(BaseModel):
    name: str


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


def test_prompt_fingerprint_is_chars_plus_short_hash_only():
    messages = [{"role": "user", "content": "hello extractor"}]
    chars, sha = prompt_fingerprint(messages)
    assert isinstance(chars, int) and chars > 0
    assert isinstance(sha, str) and len(sha) == 12
    # Deterministic for identical input.
    assert prompt_fingerprint(messages) == (chars, sha)
    # Different content changes the fingerprint, not the type shape.
    assert prompt_fingerprint([{"role": "user", "content": "other"}]) != (chars, sha)
    # No body material survives: neither return value contains the text.
    assert "hello extractor" not in str(chars) and "hello extractor" not in sha


def test_dispatch_event_prompt_fields_default_none():
    event = DispatchEvent(stage="router", model="m", tier=None,
                          purpose="initial", duration_s=1.0, outcome="ok")
    assert event.prompt_chars is None
    assert event.prompt_sha is None


def _make_client(monkeypatch) -> Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_body('{"name": "x"}'))

    async def no_sleep(delay, **kwargs):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    settings = MagicMock(spec=Settings)
    settings.llm_api_key = "test-token"
    settings.llm_base_url = "https://llm.test.local/v1"
    settings.llm_request_timeout_seconds = 90.0
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return Client(settings, http_client=http_client)


@pytest.mark.anyio
async def test_extractor_dispatch_carries_prompt_size(monkeypatch):
    client = _make_client(monkeypatch)
    token = stage_context.set("extractor")
    try:
        with collect_dispatches() as events:
            result = await client.generate_structured(
                model="test-model", prompt="size me", response_schema=_PingSchema)
        assert result.parsed is not None
        assert len(events) == 1
        event = events[0]
        assert event.stage == "extractor"
        assert isinstance(event.prompt_chars, int) and event.prompt_chars > 0
        assert isinstance(event.prompt_sha, str) and len(event.prompt_sha) == 12
    finally:
        stage_context.reset(token)


@pytest.mark.anyio
async def test_non_extractor_dispatch_has_no_prompt_size(monkeypatch):
    client = _make_client(monkeypatch)
    token = stage_context.set("router")
    try:
        with collect_dispatches() as events:
            result = await client.generate_structured(
                model="test-model", prompt="size me", response_schema=_PingSchema)
        assert result.parsed is not None
        assert len(events) == 1
        assert events[0].prompt_chars is None
        assert events[0].prompt_sha is None
    finally:
        stage_context.reset(token)
