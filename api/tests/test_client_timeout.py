"""Tests that Client correctly surfaces timeouts as ClientError.

We mock ``AsyncOpenAI.chat.completions.create`` to sleep forever, set
``llm_request_timeout_seconds`` very low (0.1s), and assert the client
raises within that window instead of hanging.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from app.core.config import Settings
from app.services.client import Client, ClientError

# -- Helpers -----------------------------------------------------------------

class _DummySchema(BaseModel):
    """Trivial schema used for structured-output tests."""
    value: str


def _fast_settings() -> Settings:
    """Return a Settings-like object with a very short timeout."""
    settings = MagicMock(spec=Settings)
    settings.llm_api_key = "test-key"
    settings.llm_request_timeout_seconds = 0.1  # 100ms — fast enough for CI
    return settings


async def _hang_forever(*args, **kwargs):
    """Coroutine that never returns, simulating a stalled endpoint."""
    await asyncio.sleep(999)


# -- Tests -------------------------------------------------------------------

@pytest.mark.anyio
async def test_generate_structured_raises_on_timeout():
    """Both schema and plain attempts time out -> ClientError raised."""
    settings = _fast_settings()

    with patch("app.services.client.AsyncOpenAI") as MockClient:
        mock_instance = MockClient.return_value
        mock_instance.chat.completions.create = AsyncMock(side_effect=_hang_forever)

        client = Client(settings)

        with pytest.raises(ClientError, match="timed out"):
            await client.generate_structured(
                model="test-model",
                prompt="hello",
                response_schema=_DummySchema,
            )


@pytest.mark.anyio
async def test_generate_structured_with_image_raises_on_timeout():
    """Vision path: both attempts time out -> ClientError raised."""
    settings = _fast_settings()

    with patch("app.services.client.AsyncOpenAI") as MockClient:
        mock_instance = MockClient.return_value
        mock_instance.chat.completions.create = AsyncMock(side_effect=_hang_forever)

        client = Client(settings)

        with pytest.raises(ClientError, match="timed out"):
            await client.generate_structured_with_image(
                model="test-model",
                prompt="extract fields",
                image_bytes=b"\x89PNG\r\n",
                image_media_type="image/png",
                response_schema=_DummySchema,
            )


@pytest.mark.anyio
async def test_schema_timeout_stops_without_format_fallback():
    """A transport timeout gets one same-mode retry, never a new format."""
    settings = _fast_settings()

    with patch("app.services.client.AsyncOpenAI") as MockClient:
        mock_instance = MockClient.return_value
        mock_create = AsyncMock(side_effect=_hang_forever)
        mock_instance.chat.completions.create = mock_create

        client = Client(settings)

        with pytest.raises(ClientError, match="timed out"):
            await client.generate_structured(
                model="test-model",
                prompt="hello",
                response_schema=_DummySchema,
            )

        assert mock_create.call_count == 2
        assert all(call.kwargs["response_format"]["type"] == "json_schema"
                   for call in mock_create.call_args_list)


@pytest.mark.anyio
async def test_generate_text_raises_on_timeout():
    """Plain generate_text also surfaces a clear timeout error."""
    settings = _fast_settings()

    with patch("app.services.client.AsyncOpenAI") as MockClient:
        mock_instance = MockClient.return_value
        mock_instance.chat.completions.create = AsyncMock(side_effect=_hang_forever)

        client = Client(settings)

        with pytest.raises(ClientError, match="timed out"):
            await client.generate_text(
                model="test-model",
                prompt="hello",
            )


@pytest.mark.anyio
async def test_timeout_logs_model_duration_category_without_content(caplog):
    """Timeout logs carry model/attempt/duration/category, never prompt bodies."""
    import logging

    settings = _fast_settings()
    with patch("app.services.client.AsyncOpenAI") as MockClient:
        mock_instance = MockClient.return_value
        mock_instance.chat.completions.create = AsyncMock(side_effect=_hang_forever)
        client = Client(settings)
        with caplog.at_level(logging.INFO, logger="app.services.client"):
            with pytest.raises(ClientError, match="timed out"):
                await client.generate_structured(
                    model="secret-model-xyz",
                    prompt="PRIVATE document content that must never appear in logs",
                    response_schema=_DummySchema,
                )
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "secret-model-xyz" in messages
    assert "attempt=" in messages or "attempts=" in messages
    assert "duration=" in messages
    assert "category=timeout" in messages
    assert "PRIVATE document content" not in messages


@pytest.mark.anyio
async def test_cancellation_releases_request_slot():
    """Cancelling a stalled generation frees the semaphore for the next call."""
    import asyncio
    from types import SimpleNamespace

    from app.services.client import Client as _Client

    entered = asyncio.Event()
    release = asyncio.Event()

    async def hanging(**kwargs):
        entered.set()
        await release.wait()
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"value":"ok"}'))],
            usage=None,
        )

    settings = _fast_settings()
    settings.llm_base_url = "https://test-slot/v1"
    settings.llm_max_concurrent_requests = 1
    with patch("app.services.client.AsyncOpenAI") as MockClient:
        mock_instance = MockClient.return_value
        mock_instance.chat.completions.create = hanging
        client = _Client(settings)
        task = asyncio.create_task(
            client.generate_structured(
                model="m", prompt="p", response_schema=_DummySchema,
            )
        )
        await entered.wait()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, ClientError):
            pass
        release.set()
        # Slot must be free: a fresh generation completes without deadlock.
        result = await asyncio.wait_for(
            client.generate_structured(
                model="m", prompt="p", response_schema=_DummySchema,
            ),
            timeout=5,
        )
        assert result.parsed is not None or result is not None
