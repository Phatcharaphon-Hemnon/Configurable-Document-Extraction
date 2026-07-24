"""Tests for SutGenAIClient.generate_structured retry path.

Verifies that on a parse failure, the retry does NOT resend the full
original prompt (which contains the document text), but instead sends
only the malformed response + schema for correction.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
for _p in (_REPO_ROOT, _BACKEND_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.services.sut_genai_client import SutGenAIClient  # noqa: E402
from app.core.config import Settings  # noqa: E402


class _DummySchema(BaseModel):
    name: str
    value: int


def _make_client():
    settings = MagicMock(spec=Settings)
    settings.github_models_token = "test-token"
    return SutGenAIClient(settings)


def _make_response(content: str, prompt_tokens: int = 10, completion_tokens: int = 5):
    """Build a fake OpenAI ChatCompletion response."""
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
async def test_retry_does_not_resend_original_prompt():
    """On parse failure, the retry should NOT contain the original prompt."""
    client = _make_client()

    original_prompt = "Extract data from this very long document text... " * 100
    malformed_json = '{"name": "test", "value": INVALID}'
    valid_json = '{"name": "test", "value": 42}'

    call_count = 0
    captured_messages: list = []

    async def mock_create(**kwargs):
        nonlocal call_count
        call_count += 1
        captured_messages.append(kwargs.get("messages", []))
        if call_count == 1:
            return _make_response(malformed_json)
        return _make_response(valid_json)

    client._client.chat.completions.create = mock_create

    result = await client.generate_structured(
        model="test-model",
        prompt=original_prompt,
        response_schema=_DummySchema,
    )

    # Two calls should have been made
    assert call_count == 2
    assert len(captured_messages) == 2

    # First call should contain the original prompt
    first_call_content = captured_messages[0][0]["content"]
    assert original_prompt[:50] in first_call_content

    # Second call (retry) should NOT contain the original prompt
    second_call_content = captured_messages[1][0]["content"]
    assert original_prompt[:50] not in second_call_content

    # Second call SHOULD contain the malformed response
    assert malformed_json in second_call_content

    # Second call should contain the schema
    assert '"name"' in second_call_content
    assert 'Schema' in second_call_content

    # Result should be valid
    assert result.parsed is not None
    assert result.parsed.name == "test"
    assert result.parsed.value == 42


@pytest.mark.anyio
async def test_retry_reports_token_usage():
    """Token usage from both attempts should be accessible."""
    client = _make_client()

    malformed = '{bad json}'
    valid = '{"name": "ok", "value": 1}'

    call_count = 0

    async def mock_create(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _make_response(malformed, prompt_tokens=100, completion_tokens=50)
        return _make_response(valid, prompt_tokens=20, completion_tokens=10)

    client._client.chat.completions.create = mock_create

    result = await client.generate_structured(
        model="test-model",
        prompt="some prompt",
        response_schema=_DummySchema,
    )

    # Final result should carry the retry attempt's token counts
    assert result.prompt_tokens == 20
    assert result.completion_tokens == 10
    assert result.total_tokens == 30
