"""Tests for Client 3-tier JSON fallback, max_tokens, and DISABLE_STRICT_JSON_SCHEMA logic.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from openai import BadRequestError
from pydantic import BaseModel

_REPO_ROOT = Path(__file__).resolve().parents[2]
_API_ROOT = Path(__file__).resolve().parents[1]
for _p in (_REPO_ROOT, _API_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.core.config import Settings
from app.services.client import Client, ClientError


class _DummySchema(BaseModel):
    name: str
    value: int


def _make_client(disable_strict: bool = False):
    settings = MagicMock(spec=Settings)
    settings.llm_api_key = "test-token"
    settings.llm_base_url = "https://api.openai.com/v1"
    settings.llm_request_timeout_seconds = 90.0
    settings.disable_strict_json_schema = disable_strict
    return Client(settings)


def _make_response(content: str, prompt_tokens: int = 10, completion_tokens: int = 5):
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = MagicMock()
    resp.usage.prompt_tokens = prompt_tokens
    resp.usage.completion_tokens = completion_tokens
    resp.usage.total_tokens = prompt_tokens + completion_tokens
    resp.model_dump = MagicMock(return_value={"id": "test"})
    return resp


@pytest.mark.anyio
async def test_three_tier_fallback_order():
    """Verify call order: json_schema (strict) -> json_object -> plain prompt fallback."""
    client = _make_client()

    malformed1 = "malformed json attempt 1"
    malformed2 = "malformed json attempt 2"
    valid = '{"name": "valid", "value": 100}'

    call_count = 0
    captured_kwargs: list[dict] = []

    async def mock_create(**kwargs):
        nonlocal call_count
        call_count += 1
        captured_kwargs.append(kwargs)
        if call_count == 1:
            return _make_response(malformed1)
        elif call_count == 2:
            return _make_response(malformed2)
        return _make_response(valid)

    client._client.chat.completions.create = mock_create

    result = await client.generate_structured(
        model="test-model",
        prompt="test prompt",
        response_schema=_DummySchema,
    )

    assert call_count == 3
    assert len(captured_kwargs) == 3

    # Call 1: json_schema
    assert captured_kwargs[0]["response_format"]["type"] == "json_schema"
    assert captured_kwargs[0]["response_format"]["json_schema"]["strict"] is True

    # Call 2: json_object
    assert captured_kwargs[1]["response_format"]["type"] == "json_object"

    # Call 3: plain (no response_format)
    assert "response_format" not in captured_kwargs[2]

    assert result.parsed is not None
    assert result.parsed.name == "valid"
    assert result.parsed.value == 100


@pytest.mark.anyio
async def test_error_message_contains_raw_preview():
    """When all attempts fail, ClientError contains raw response preview."""
    client = _make_client()
    bad_output = "THIS_IS_VERY_BAD_MALFORMED_OUTPUT_PREVIEW_TEST"

    async def mock_create(**kwargs):
        return _make_response(bad_output)

    client._client.chat.completions.create = mock_create

    with pytest.raises(ClientError) as exc_info:
        await client.generate_structured_with_image(
            model="test-model",
            prompt="test prompt",
            image_bytes=b"fakeimagebytes",
            image_media_type="image/jpeg",
            response_schema=_DummySchema,
        )

    err_msg = str(exc_info.value)
    assert bad_output in err_msg
    assert "Raw response preview:" in err_msg


@pytest.mark.anyio
async def test_max_tokens_passed_through():
    """Verify max_tokens is passed down to OpenAI create call."""
    client = _make_client()
    valid = '{"name": "test", "value": 1}'

    captured_kwargs: list[dict] = []

    async def mock_create(**kwargs):
        captured_kwargs.append(kwargs)
        return _make_response(valid)

    client._client.chat.completions.create = mock_create

    await client.generate_structured_with_image(
        model="test-model",
        prompt="test prompt",
        image_bytes=b"fake",
        image_media_type="image/png",
        response_schema=_DummySchema,
        max_tokens=4096,
    )

    assert len(captured_kwargs) == 1
    assert captured_kwargs[0]["max_tokens"] == 4096


@pytest.mark.anyio
async def test_disable_strict_skips_tier_one():
    """When DISABLE_STRICT_JSON_SCHEMA=true, Tier 1 (json_schema) is skipped."""
    client = _make_client(disable_strict=True)
    valid = '{"name": "test", "value": 42}'

    captured_kwargs: list[dict] = []

    async def mock_create(**kwargs):
        captured_kwargs.append(kwargs)
        return _make_response(valid)

    client._client.chat.completions.create = mock_create

    result = await client.generate_structured(
        model="test-model",
        prompt="test prompt",
        response_schema=_DummySchema,
    )

    assert len(captured_kwargs) == 1
    # First call should be json_object, not json_schema
    assert captured_kwargs[0]["response_format"]["type"] == "json_object"
    assert result.parsed is not None
    assert result.parsed.value == 42


# ---------------------------------------------------------------------------
# disable_reasoning / extra_body tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_disable_reasoning_sends_extra_body():
    """When disable_reasoning=True, extra_body with reasoning disabled should be in kwargs."""
    client = _make_client()
    valid = '{"name": "test", "value": 7}'

    captured_kwargs: list[dict] = []

    async def mock_create(**kwargs):
        captured_kwargs.append(kwargs)
        return _make_response(valid)

    client._client.chat.completions.create = mock_create

    await client.generate_structured(
        model="test-model",
        prompt="test prompt",
        response_schema=_DummySchema,
        disable_reasoning=True,
    )

    # At least one call should have been made
    assert len(captured_kwargs) >= 1
    # First call (schema tier) should include extra_body
    assert "extra_body" in captured_kwargs[0]
    assert captured_kwargs[0]["extra_body"] == {"reasoning": {"enabled": False}}


@pytest.mark.anyio
async def test_no_reasoning_extra_body_by_default():
    """When disable_reasoning is not passed (default False), extra_body should NOT be present."""
    client = _make_client()
    valid = '{"name": "test", "value": 9}'

    captured_kwargs: list[dict] = []

    async def mock_create(**kwargs):
        captured_kwargs.append(kwargs)
        return _make_response(valid)

    client._client.chat.completions.create = mock_create

    await client.generate_structured(
        model="test-model",
        prompt="test prompt",
        response_schema=_DummySchema,
    )

    assert len(captured_kwargs) >= 1
    # No call should contain extra_body
    for kw in captured_kwargs:
        assert "extra_body" not in kw


@pytest.mark.anyio
async def test_reasoning_extra_body_retry_on_rejection():
    """If provider rejects extra_body, client retries same tier without it."""
    client = _make_client()
    valid = '{"name": "recovered", "value": 99}'

    call_count = 0
    captured_kwargs: list[dict] = []

    async def mock_create(**kwargs):
        nonlocal call_count
        call_count += 1
        captured_kwargs.append(dict(kwargs))
        # First call has extra_body → reject it
        if call_count == 1 and "extra_body" in kwargs:
            raise BadRequestError("Unsupported parameter: extra_body",
                                  response=httpx.Response(400, request=httpx.Request("POST", "https://test/v1")),
                                  body=None)
        # Second call (retry without extra_body on same tier) → succeed
        return _make_response(valid)

    client._client.chat.completions.create = mock_create

    result = await client.generate_structured(
        model="test-model",
        prompt="test prompt",
        response_schema=_DummySchema,
        disable_reasoning=True,
    )

    # Should have at least 2 calls: first with extra_body (rejected), second without
    assert call_count >= 2

    # First call should have extra_body
    assert "extra_body" in captured_kwargs[0]
    assert captured_kwargs[0]["extra_body"] == {"reasoning": {"enabled": False}}

    # Second call should NOT have extra_body (same-tier retry)
    assert "extra_body" not in captured_kwargs[1]

    # Should still be on the same tier (json_schema)
    assert captured_kwargs[0].get("response_format", {}).get("type") == "json_schema"
    assert captured_kwargs[1].get("response_format", {}).get("type") == "json_schema"

    # Result should parse successfully
    assert result.parsed is not None
    assert result.parsed.name == "recovered"
    assert result.parsed.value == 99
