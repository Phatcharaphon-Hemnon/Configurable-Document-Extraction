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
