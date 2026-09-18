"""Ollama-local Qwen thinking-model auto-off (measured 2026-09-18).

Isolated (no network/credentials): the transport is stubbed. Live
confirmation happened in the bounded probe (reasoning_effort=none:
9s/55tok vs baseline 57s/392tok), never here.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.services.client import (  # noqa: E402
    Client,
    _classify_parse_failure,
    is_qwen_thinking_model,
)


class _S(BaseModel):
    name: str
    value: int


def _settings(**overrides):
    s = MagicMock()
    s.llm_api_key = "test-token"
    s.llm_base_url = "http://100.123.255.72:11434/v1"
    s.llm_provider = "ollama-local"
    s.llm_request_timeout_seconds = 90.0
    s.disable_strict_json_schema = False
    s.llm_temperature = 0.0
    s.llm_max_concurrent_requests = 1
    s.llm_reasoning_effort = ""
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _resp(content: str):
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 5
    usage.total_tokens = 15
    resp.usage = usage
    resp.model_dump = MagicMock(return_value={"id": "test"})
    return resp


@pytest.fixture
def memories():
    before_tiers = set(Client._unsupported_tiers)
    before_reason = set(Client._reasoning_unsupported)
    yield
    Client._unsupported_tiers.difference_update(Client._unsupported_tiers - before_tiers)
    Client._unsupported_tiers.intersection_update(before_tiers)
    Client._reasoning_unsupported.difference_update(Client._reasoning_unsupported - before_reason)
    Client._reasoning_unsupported.intersection_update(before_reason)


def test_predicate_matches_qwen_thinking_families():
    assert is_qwen_thinking_model("qwen3:8b")
    assert is_qwen_thinking_model("qwen3:32b")
    assert is_qwen_thinking_model("qwq:32b")
    assert is_qwen_thinking_model("hf.co/Qwen/Qwen3-8B-GGUF:Q8_0")
    assert not is_qwen_thinking_model("qwen2.5:3b")
    assert not is_qwen_thinking_model("openai/gpt-oss-20b")
    assert not is_qwen_thinking_model("scb10x/llama3.2-typhoon2-3b-instruct")
    assert not is_qwen_thinking_model("")


@pytest.mark.asyncio
async def test_qwen_sends_reasoning_none_and_temp_07(memories):
    client = Client(_settings())
    seen: dict = {}

    async def mock_create(**kwargs):
        seen.update(kwargs)
        return _resp('{"name": "ok", "value": 1}')

    client._client.chat.completions.create = mock_create
    result = await client.generate_structured(
        model="qwen3:8b", prompt="p", response_schema=_S, disable_reasoning=True)
    assert result.parsed is not None and result.parsed.name == "ok"
    assert seen.get("reasoning_effort") == "none"
    assert "extra_body" not in seen  # effort replaces legacy flag, never both
    assert seen.get("temperature") == 0.7
    assert result.reasoning_effort == "none"


@pytest.mark.asyncio
async def test_non_qwen_ollama_unchanged(memories):
    client = Client(_settings())
    seen: dict = {}

    async def mock_create(**kwargs):
        seen.update(kwargs)
        return _resp('{"name": "ok", "value": 1}')

    client._client.chat.completions.create = mock_create
    result = await client.generate_structured(
        model="qwen2.5:3b", prompt="p", response_schema=_S, disable_reasoning=True)
    assert result.parsed is not None
    assert "reasoning_effort" not in seen
    assert seen["extra_body"] == {"reasoning": {"enabled": False}}
    assert seen.get("temperature") == 0.0
    assert result.reasoning_effort is None


@pytest.mark.asyncio
async def test_explicit_temperature_wins_over_qwen_default(memories):
    client = Client(_settings())
    seen: dict = {}

    async def mock_create(**kwargs):
        seen.update(kwargs)
        return _resp('{"name": "ok", "value": 1}')

    client._client.chat.completions.create = mock_create
    await client.generate_structured(
        model="qwen3:8b", prompt="p", response_schema=_S,
        temperature=0.2, disable_reasoning=True)
    assert seen.get("temperature") == 0.2
    assert seen.get("reasoning_effort") == "none"


@pytest.mark.asyncio
async def test_non_ollama_qwen_gets_no_auto_none(memories):
    s = _settings(llm_provider="openrouter",
                  llm_base_url="https://openrouter.ai/api/v1")
    client = Client(s)
    seen: dict = {}

    async def mock_create(**kwargs):
        seen.update(kwargs)
        return _resp('{"name": "ok", "value": 1}')

    client._client.chat.completions.create = mock_create
    result = await client.generate_structured(
        model="qwen3:8b", prompt="p", response_schema=_S, disable_reasoning=True)
    assert result.parsed is not None
    assert "reasoning_effort" not in seen
    assert seen["extra_body"] == {"reasoning": {"enabled": False}}
    assert seen.get("temperature") == 0.0


def test_empty_with_length_finish_is_truncated():
    raw_response = {"choices": [{"finish_reason": "length"}]}
    d = _classify_parse_failure(_S, "<think>reasoning…</think>", raw_response)
    assert d.kind == "truncated"
    assert d.truncated is True


def test_empty_with_stop_finish_stays_empty():
    raw_response = {"choices": [{"finish_reason": "stop"}]}
    d = _classify_parse_failure(_S, "<think>reasoning…</think>", raw_response)
    assert d.kind == "empty"
    assert d.truncated is False
