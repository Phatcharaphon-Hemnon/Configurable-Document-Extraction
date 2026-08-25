"""Rate-limit handling: 429/503 ResourceExhausted must back off and retry
the SAME call instead of degrading to the next fallback tier."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_API_ROOT = Path(__file__).resolve().parents[1]
for _p in (_REPO_ROOT, _API_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.services.sut_genai_client import (  # noqa: E402
    RATE_LIMIT_BACKOFF_SECONDS,
    RATE_LIMIT_MAX_RETRIES,
    SutGenAIClient,
    _is_rate_limit_error,
)


def _client_with_responses(responses):
    """SutGenAIClient whose OpenAI client returns *responses* in order."""
    client = SutGenAIClient.__new__(SutGenAIClient)
    client._timeout = 5.0
    client._client = MagicMock()
    calls = {"n": 0}

    async def create(**kwargs):
        i = calls["n"]
        calls["n"] += 1
        item = responses[min(i, len(responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    client._client.chat.completions.create = create
    return client, calls


def test_rate_limit_detection():
    assert _is_rate_limit_error(Exception("Error code: 503 - ResourceExhausted: Worker local total request limit reached (16/16)"))
    assert _is_rate_limit_error(Exception("429 Too Many Requests"))
    assert _is_rate_limit_error(Exception("quota exceeded"))
    assert not _is_rate_limit_error(Exception("invalid schema"))
    assert not _is_rate_limit_error(asyncio.TimeoutError())


@pytest.mark.asyncio
async def test_rate_limit_retries_same_call_then_succeeds(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    ok = object()
    client, calls = _client_with_responses([
        Exception("Error code: 503 - ResourceExhausted: Worker local total request limit reached (16/16)"),
        ok,
    ])

    result = await client._chat_with_retry(model="m", messages=[])
    assert result is ok
    assert calls["n"] == 2
    assert sleeps and sleeps[0] == RATE_LIMIT_BACKOFF_SECONDS


@pytest.mark.asyncio
async def test_rate_limit_gives_up_after_max_retries(monkeypatch):
    async def fake_sleep(seconds):
        pass

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    err = Exception("Error code: 503 - ResourceExhausted")
    client, calls = _client_with_responses([err])

    with pytest.raises(Exception, match="ResourceExhausted"):
        await client._chat_with_retry(model="m", messages=[])
    assert calls["n"] == RATE_LIMIT_MAX_RETRIES


@pytest.mark.asyncio
async def test_non_rate_limit_error_not_retried(monkeypatch):
    client, calls = _client_with_responses([Exception("invalid request schema")])

    with pytest.raises(Exception, match="invalid request schema"):
        await client._chat_with_retry(model="m", messages=[])
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_timeout_not_swallowed(monkeypatch):
    client, calls = _client_with_responses([asyncio.TimeoutError()])

    with pytest.raises(asyncio.TimeoutError):
        await client._chat_with_retry(model="m", messages=[])
    assert calls["n"] == 1
