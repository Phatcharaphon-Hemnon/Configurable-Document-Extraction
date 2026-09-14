"""xKiro gateway provider: credentials, errors, reasoning scope (offline).

All transport is stubbed — no network, no credentials. Live behavior
(tier support, free-model defaults, reasoning effect) is Phase B, blocked.
Docs references below are documented 2026-09-14, not live-verified.
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

from app.core.config import Settings  # noqa: E402
from app.services.client import Client, ClientError  # noqa: E402

_ENDPOINT = "https://api.xkiro.com/v1"

_XKIRO_402 = (
    "Error code: 402 - {'error': {'message': 'Insufficient balance. "
    "Top up your wallet to continue.', 'type': 'insufficient_quota', "
    "'code': 'insufficient_quota'}}"
)
_XKIRO_403 = (
    "Error code: 403 - {'error': {'message': 'This is a premium model. "
    "The Free plan only allows free models.', 'type': 'permission_error', "
    "'code': 'permission_denied'}}"
)
_XKIRO_500 = (
    "Error code: 500 - {'error': {'message': 'Internal error.', "
    "'type': 'server_error', 'code': 'internal_error'}}"
)
_XKIRO_502 = (
    "Error code: 502 - {'error': {'message': 'Upstream unreachable.', "
    "'type': 'server_error', 'code': 'bad_gateway'}}"
)


class _DummySchema(BaseModel):
    name: str

def _ok_resp():
    msg = MagicMock()
    msg.content = '{"name": "ok"}'
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    usage = MagicMock()
    usage.prompt_tokens = 1
    usage.completion_tokens = 1
    usage.total_tokens = 2
    resp.usage = usage
    resp.model_dump = MagicMock(return_value={"id": "t"})
    return resp




@pytest.fixture
def clean_env(monkeypatch):
    for var in (
        "LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL",
        "LLM_TEMPERATURE", "XKIRO_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def _xkiro_settings(**overrides):
    settings = MagicMock(spec=Settings)
    settings.llm_provider = "xkiro"
    settings.llm_api_key = "test-token"
    settings.llm_base_url = _ENDPOINT
    settings.llm_request_timeout_seconds = 90.0
    settings.disable_strict_json_schema = False
    settings.llm_temperature = 0.0
    settings.llm_max_concurrent_requests = 1
    settings.llm_reasoning_effort = ""
    for k, v in overrides.items():
        setattr(settings, k, v)
    return settings


# ------------------------------------------------------------------
# Credentials: isolation, fallback, missing key
# ------------------------------------------------------------------

def test_xkiro_key_not_reused_by_other_providers(clean_env):
    # XKIRO_API_KEY must never authenticate a different provider.
    clean_env.setenv("XKIRO_API_KEY", "sk-xt-test")
    clean_env.setenv("LLM_PROVIDER", "openai")
    clean_env.setenv("LLM_MODEL", "gpt-5.4-mini")
    assert Settings().llm_api_key == ""


def test_other_native_keys_not_reused_by_xkiro(clean_env):
    # Another provider's key must never authenticate xKiro.
    clean_env.setenv("OPENAI_API_KEY", "sk-openai-test")
    clean_env.setenv("OPENROUTER_API_KEY", "sk-or-test")
    clean_env.setenv("LLM_PROVIDER", "xkiro")
    clean_env.setenv("LLM_MODEL", "openai/gpt-5.4-mini")
    assert Settings().llm_api_key == ""


def test_xkiro_native_key_fallback(clean_env):
    clean_env.setenv("LLM_PROVIDER", "xkiro")
    clean_env.setenv("LLM_MODEL", "openai/gpt-5.4-mini")
    clean_env.setenv("XKIRO_API_KEY", "sk-xt-test")
    assert Settings().llm_api_key == "sk-xt-test"


def test_xkiro_explicit_key_wins_over_native(clean_env):
    clean_env.setenv("LLM_PROVIDER", "xkiro")
    clean_env.setenv("LLM_MODEL", "openai/gpt-5.4-mini")
    clean_env.setenv("LLM_API_KEY", "explicit")
    clean_env.setenv("XKIRO_API_KEY", "native")
    assert Settings().llm_api_key == "explicit"


def test_xkiro_missing_key_resolves_empty(clean_env):
    # No startup crash on a missing key: the provider surfaces 401 at call
    # time (covered below by the redaction test's error path).
    clean_env.setenv("LLM_PROVIDER", "xkiro")
    clean_env.setenv("LLM_MODEL", "openai/gpt-5.4-mini")
    assert Settings().llm_api_key == ""


# ------------------------------------------------------------------
# 402 / 403 fail fast: one attempt, actionable redacted error
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_xkiro_402_fails_fast_with_dashboard_link():
    client = Client(_xkiro_settings())
    calls = 0

    async def mock_create(**kwargs):
        nonlocal calls
        calls += 1
        raise Exception(_XKIRO_402)

    client._client.chat.completions.create = mock_create
    with pytest.raises(ClientError) as excinfo:
        await client.generate_structured(
            model="openai/gpt-5.4-mini", prompt="p", response_schema=_DummySchema)
    assert calls == 1  # no automatic retry on billing errors
    assert "xkiro.com/dashboard" in str(excinfo.value)


@pytest.mark.asyncio
async def test_xkiro_403_permission_fails_fast_without_retry():
    client = Client(_xkiro_settings())
    calls = 0

    async def mock_create(**kwargs):
        nonlocal calls
        calls += 1
        raise Exception(_XKIRO_403)

    client._client.chat.completions.create = mock_create
    with pytest.raises(ClientError):
        await client.generate_structured(
            model="openai/gpt-5.4-mini", prompt="p", response_schema=_DummySchema)
    assert calls == 1


@pytest.mark.asyncio
async def test_provider_errors_redact_secrets():
    client = Client(_xkiro_settings())
    secret = "sk-xt-fake-secret-value"

    async def mock_create(**kwargs):
        raise Exception(f"401 Unauthorized: Bearer {secret} rejected")

    client._client.chat.completions.create = mock_create
    with pytest.raises(ClientError) as excinfo:
        await client.generate_structured(
            model="openai/gpt-5.4-mini", prompt="p", response_schema=_DummySchema)
    assert secret not in str(excinfo.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [_XKIRO_500, _XKIRO_502])
async def test_500_502_surface_immediately_as_compatibility_limitation(body):
    """500/502 are NOT retried by this transport (it covers 429/503 only).

    Documented here as a compatibility limitation vs xKiro's retry guidance
    (retry 429/500/502/503 per https://docs.xkiro.com/api/errors/,
    documented 2026-09-14, not live-verified) — not as measured behavior.
    """
    client = Client(_xkiro_settings())
    calls = 0

    async def mock_create(**kwargs):
        nonlocal calls
        calls += 1
        raise Exception(body)

    client._client.chat.completions.create = mock_create
    with pytest.raises(ClientError, match="LLM API call failed"):
        await client.generate_structured(
            model="openai/gpt-5.4-mini", prompt="p", response_schema=_DummySchema)
    assert calls == 1


# ------------------------------------------------------------------
# Reasoning scope: "none" is per-model; xKiro has no allowlisted pair yet
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reasoning_none_never_sent_alongside_extra_body():
    client = Client(_xkiro_settings())
    seen: dict = {}

    async def mock_create(**kwargs):
        seen.update(kwargs)
        return _ok_resp()

    client._client.chat.completions.create = mock_create
    result = await client.generate_structured(
        model="openai/gpt-5.4-mini", prompt="p", response_schema=_DummySchema,
        disable_reasoning=True, reasoning_effort="none")
    assert result.parsed is not None
    assert seen.get("reasoning_effort") == "none"
    assert "extra_body" not in seen
    assert result.reasoning_effort == "none"


@pytest.mark.asyncio
async def test_env_effort_ignored_for_xkiro_without_allowlisted_pair():
    """The NVIDIA-only allowlist must not silently enable xKiro, nor may
    xKiro silently gain it: with no exact pair allowlisted, env resolves
    to inactive and requests stay byte-identical to current behavior."""
    import app.services.client as client_mod

    assert all(ep != _ENDPOINT for ep, _m in client_mod.REASONING_EFFORT_ALLOWLIST)
    client = Client(_xkiro_settings(llm_reasoning_effort="low"))
    seen: dict = {}

    async def mock_create(**kwargs):
        seen.update(kwargs)
        return _ok_resp()

    client._client.chat.completions.create = mock_create
    result = await client.generate_structured(
        model="openai/gpt-5.4-mini", prompt="p", response_schema=_DummySchema)
    assert "reasoning_effort" not in seen
    assert result.reasoning_effort is None
