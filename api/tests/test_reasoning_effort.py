"""Reasoning-effort override: scoping, fallback separation, fingerprints.

Isolated (no network/credentials): the transport is stubbed. Live
confirmation of `reasoning_effort` support happens in the bounded
diagnostic, never here.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

import app.services.client as client_mod  # noqa: E402
from app.services.client import Client  # noqa: E402


class _S(BaseModel):
    name: str
    value: int


ENDPOINT = "https://integrate.api.nvidia.com/v1"
MODEL = "openai/gpt-oss-20b"


@pytest.fixture
def memories():
    before_tiers = set(Client._unsupported_tiers)
    before_reason = set(Client._reasoning_unsupported)
    yield
    Client._unsupported_tiers.difference_update(Client._unsupported_tiers - before_tiers)
    Client._unsupported_tiers.intersection_update(before_tiers)
    Client._reasoning_unsupported.difference_update(Client._reasoning_unsupported - before_reason)
    Client._reasoning_unsupported.intersection_update(before_reason)


def _settings(**overrides):
    s = MagicMock()
    s.llm_api_key = "test-token"
    s.llm_base_url = ENDPOINT
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


@pytest.mark.asyncio
async def test_default_sends_no_reasoning_param(memories):
    client = Client(_settings())
    seen: dict = {}

    async def mock_create(**kwargs):
        seen.update(kwargs)
        return _resp('{"name": "ok", "value": 1}')

    client._client.chat.completions.create = mock_create
    result = await client.generate_structured(
        model=MODEL, prompt="p", response_schema=_S, disable_reasoning=True)
    assert result.parsed is not None and result.parsed.name == "ok"
    assert "reasoning_effort" not in seen  # current behavior, byte-identical
    assert seen["extra_body"] == {"reasoning": {"enabled": False}}
    assert result.reasoning_effort is None


@pytest.mark.asyncio
async def test_explicit_low_replaces_extra_body(memories):
    client = Client(_settings())
    seen: dict = {}

    async def mock_create(**kwargs):
        seen.update(kwargs)
        return _resp('{"name": "ok", "value": 1}')

    client._client.chat.completions.create = mock_create
    result = await client.generate_structured(
        model=MODEL, prompt="p", response_schema=_S,
        disable_reasoning=True, reasoning_effort="low")
    assert result.parsed is not None
    assert seen.get("reasoning_effort") == "low"
    assert "extra_body" not in seen  # never send both
    assert result.reasoning_effort == "low"


@pytest.mark.asyncio
async def test_env_override_gated_by_exact_allowlist(memories, monkeypatch):
    # Verified pair allowlisted in code: env value applied there...
    client = Client(_settings(llm_reasoning_effort="low"))
    seen: dict = {}

    async def mock_create(**kwargs):
        seen.update(kwargs)
        return _resp('{"name": "ok", "value": 1}')

    client._client.chat.completions.create = mock_create
    result = await client.generate_structured(model=MODEL, prompt="p", response_schema=_S)
    assert seen.get("reasoning_effort") == "low"
    assert result.reasoning_effort == "low"

    # ...but ignored for any other model (no substring matching).
    seen.clear()
    await client.generate_structured(model="meta/llama-3.2-11b-vision-instruct",
                                     prompt="p", response_schema=_S)
    assert "reasoning_effort" not in seen

    # Empty allowlist: env value ignored everywhere (gating mechanism).
    monkeypatch.setattr(client_mod, "REASONING_EFFORT_ALLOWLIST", {})
    seen.clear()
    await client.generate_structured(model=MODEL, prompt="p", response_schema=_S)
    assert "reasoning_effort" not in seen


@pytest.mark.asyncio
async def test_explicit_unlisted_level_clamped_for_known_pair(memories, caplog):
    """A known pair with an unlisted level (e.g. "none" on GPT-OSS) is never
    sent: known-unsupported values resolve to inactive with a warning."""
    import logging

    client = Client(_settings())
    seen: dict = {}

    async def mock_create(**kwargs):
        seen.update(kwargs)
        return _resp('{"name": "ok", "value": 1}')

    client._client.chat.completions.create = mock_create
    with caplog.at_level(logging.WARNING, logger="app.services.client"):
        result = await client.generate_structured(
            model=MODEL, prompt="p", response_schema=_S, reasoning_effort="none")
    assert "reasoning_effort" not in seen
    assert "extra_body" not in seen  # clamped to inactive: nothing sent
    assert result.reasoning_effort is None
    assert any("not established" in r.message for r in caplog.records)


class _UnsupportedParam(Exception):
    """Real exception carrying an SDK-style 400 body (MagicMock can't raise)."""

    def __init__(self, msg: str):
        super().__init__(msg)
        self.status_code = 400
        self.body = {"error": {"message": msg, "type": "invalid_request_error"}}
        self.code = None
        self.param = None
        self.type = "invalid_request_error"
        self.request_id = None
        self.response = None


def _unsupported_param_error():
    return _UnsupportedParam("Unsupported parameter: 'reasoning_effort'")


@pytest.mark.asyncio
async def test_reasoning_rejection_removes_param_without_tier_downgrade(memories):
    client = Client(_settings())
    calls: list[dict] = []

    async def mock_create(**kwargs):
        calls.append(dict(kwargs))
        if "reasoning_effort" in kwargs:
            raise _unsupported_param_error()
        return _resp('{"name": "ok", "value": 1}')

    client._client.chat.completions.create = mock_create
    result = await client.generate_structured(
        model=MODEL, prompt="p", response_schema=_S, reasoning_effort="low")
    assert result.parsed is not None and result.parsed.name == "ok"
    # Same tier retried with only the param removed (format untouched).
    assert calls[0].get("reasoning_effort") == "low"
    assert "reasoning_effort" not in calls[1]
    assert calls[0]["response_format"] == calls[1]["response_format"]
    assert calls[0]["response_format"]["type"] == "json_schema"
    # Output-format tier NOT marked unsupported by a param rejection.
    assert (ENDPOINT, MODEL, "json_schema") not in Client._unsupported_tiers
    # Effective setting recorded after fallback.
    assert result.reasoning_effort == ""
    # Later calls skip the param without burning an attempt on it.
    calls.clear()
    result2 = await client.generate_structured(
        model=MODEL, prompt="p", response_schema=_S, reasoning_effort="low")
    assert result2.parsed is not None
    assert len(calls) == 1 and "reasoning_effort" not in calls[0]


def test_config_rejects_invalid_effort_values(monkeypatch):

    from app.core.config import Settings

    monkeypatch.setenv("LLM_REASONING_EFFORT", "ultra")
    assert Settings().llm_reasoning_effort == ""
    monkeypatch.setenv("LLM_REASONING_EFFORT", "LOW")
    assert Settings().llm_reasoning_effort == "low"


def test_fingerprint_includes_reasoning_effort(tmp_path, monkeypatch):
    from app.services.result_cache import ResultCache

    s = _settings()
    s.cache_path = str(tmp_path / "cache")
    s.result_cache_enabled = True
    s.result_cache_ttl_seconds = 100.0
    s.result_cache_max_entries = 8
    cache = ResultCache(s)
    base = cache.manifest_key(file_bytes=b"d", filename="a.png", page_count=1)
    monkeypatch.setattr(s, "llm_reasoning_effort", "low", raising=False)
    changed = cache.manifest_key(file_bytes=b"d", filename="a.png", page_count=1)
    assert base != changed  # old manifests miss after the setting changes


@pytest.mark.asyncio
async def test_queue_wait_is_logged(caplog):
    from app.services.request_control import limited_generation

    class _Obj:
        def __init__(self):
            self.settings = _settings()

        @limited_generation
        async def go(self):
            return "done"

    with caplog.at_level(logging.INFO, logger="app.services.request_control"):
        assert await _Obj().go() == "done"
    assert any("provider queue wait" in r.message for r in caplog.records)
