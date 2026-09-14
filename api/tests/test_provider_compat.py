"""Provider compatibility: profiles, builder, memory, parity (offline).

Mocked transport throughout — no network, no credentials. Live support
claims live in docs/reference/provider_compatibility.md (documented, not
live-verified); nothing here asserts live behavior.
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

from app.services.client import Client  # noqa: E402
from app.services.provider_capabilities import (  # noqa: E402
    COMPAT_POLICY_VERSION,
    build_chat_kwargs,
    endpoint_host,
    resolve_capabilities,
)


class _S(BaseModel):
    name: str
    value: int


GROQ_EP = "https://api.groq.com/openai/v1"
NVIDIA_EP = "https://integrate.api.nvidia.com/v1"
GPTOSS = "openai/gpt-oss-20b"


@pytest.fixture
def memories():
    before_tiers = set(Client._unsupported_tiers)
    before_reason = set(Client._reasoning_unsupported)
    before_ts = dict(Client._capability_memory_ts)
    yield
    Client._unsupported_tiers.intersection_update(before_tiers)
    Client._unsupported_tiers.difference_update(Client._unsupported_tiers - before_tiers)
    Client._reasoning_unsupported.intersection_update(before_reason)
    Client._reasoning_unsupported.difference_update(Client._reasoning_unsupported - before_reason)
    Client._capability_memory_ts.clear()
    Client._capability_memory_ts.update(before_ts)


def _settings(endpoint="https://api.openai.com/v1", **overrides):
    s = MagicMock()
    s.llm_provider = "openai"
    s.llm_api_key = "test-token"
    s.llm_base_url = endpoint
    s.llm_request_timeout_seconds = 90.0
    s.disable_strict_json_schema = False
    s.llm_temperature = 0.0
    s.llm_max_concurrent_requests = 1
    s.llm_reasoning_effort = ""
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _resp(content: str = '{"name": "ok", "value": 1}'):
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


# ------------------------------------------------------------------
# Resolution: exact verified rows, provider defaults, unknown hosts
# ------------------------------------------------------------------

def test_verified_groq_pair_profile():
    p = resolve_capabilities(endpoint=GROQ_EP, model=GPTOSS, provider_label="groq")
    assert p.source.startswith("verified:")
    assert p.strict_first is True
    assert p.omit_extra_body_reasoning is True
    assert p.reasoning_levels == ("low", "medium", "high")
    assert "none" not in p.reasoning_levels
    assert p.token_param == "max_tokens"


def test_verified_nvidia_pair_profile():
    p = resolve_capabilities(endpoint=NVIDIA_EP, model=GPTOSS, provider_label="nvidia")
    assert p.source.startswith("verified:")
    assert p.reasoning_levels == ("low", "medium", "high")


def test_no_substring_or_prefix_matching():
    # Near-misses must NOT inherit verified capabilities.
    variants = [
        (GROQ_EP + "/", GPTOSS),  # trailing slash is normalized — same pair
        ("https://evil-api.groq.com/openai/v1", GPTOSS),
        ("https://api.groq.com/openai/v1 extra", GPTOSS),
        (GROQ_EP, "openai/gpt-oss-20b-tweaked"),
        (GROQ_EP, "gpt-oss-20b"),  # bare name is a different ID
    ]
    for ep, model in variants:
        p = resolve_capabilities(endpoint=ep, model=model, provider_label="groq")
        if (ep.rstrip("/"), model) == (GROQ_EP, GPTOSS):
            assert p.source.startswith("verified:")
        else:
            assert not p.source.startswith("verified:"), (ep, model)


def test_provider_default_and_unknown_conservative():
    p = resolve_capabilities(endpoint="https://api.openai.com/v1",
                             model="gpt-5.4-mini", provider_label="openai")
    assert p.source == "provider-default:openai"
    assert p.strict_first is True
    assert p.omit_extra_body_reasoning is False
    assert p.reasoning_levels == ()
    u = resolve_capabilities(endpoint="https://my-gateway.internal/v1",
                             model="custom-model", provider_label="")
    assert u.source == "unknown-conservative"
    assert u.strict_first is True  # optimistic probe, NOT a support claim
    assert u.omit_extra_body_reasoning is False  # current behavior preserved


def test_endpoint_host_exact_matching():
    assert endpoint_host("https://api.groq.com/openai/v1") == "api.groq.com"
    assert endpoint_host("https://API.GROQ.COM/openai/v1") == "api.groq.com"
    assert endpoint_host("https://api.groq.com.evil.example/v1") == "api.groq.com.evil.example"
    assert endpoint_host("not a url") == ""
    assert endpoint_host("") == ""


# ------------------------------------------------------------------
# Shared builder rules (identical for every agent/path)
# ------------------------------------------------------------------

def test_builder_reasoning_replaces_extra_body_never_both():
    kw = build_chat_kwargs(
        model="m", messages=[], temperature=0.0, token_param="max_tokens",
        max_tokens=100, response_format={"type": "json_object"},
        reasoning_effort="low", disable_reasoning=True, omit_extra_body_reasoning=False)
    assert kw["reasoning_effort"] == "low"
    assert "extra_body" not in kw
    assert kw["max_tokens"] == 100
    assert kw["response_format"] == {"type": "json_object"}


def test_builder_legacy_flag_only_when_allowed():
    kw = build_chat_kwargs(
        model="m", messages=[], temperature=0.0, token_param="max_tokens",
        max_tokens=None, response_format=None,
        reasoning_effort="", disable_reasoning=True, omit_extra_body_reasoning=False)
    assert kw["extra_body"] == {"reasoning": {"enabled": False}}
    assert "max_tokens" not in kw and "response_format" not in kw
    kw2 = build_chat_kwargs(
        model="m", messages=[], temperature=0.0, token_param="max_tokens",
        max_tokens=None, response_format=None,
        reasoning_effort="", disable_reasoning=True, omit_extra_body_reasoning=True)
    assert "extra_body" not in kw2  # Groq-style omit before first request


# ------------------------------------------------------------------
# Native vs legacy (openai-label + Groq URL) parity
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_groq_native_legacy_request_parity(memories):
    """Same effective endpoint/model/settings → identical payloads (auth
    excluded: it lives in the transport, never in kwargs)."""
    seen: dict[str, dict] = {}

    async def make_client(provider, base_url, timeout):
        # Distinct timeouts force distinct shared transports: same-endpoint
        # clients otherwise share one transport object (by design) and a
        # later mock would shadow the earlier one.
        client = Client(_settings(base_url, llm_provider=provider,
                                  llm_request_timeout_seconds=timeout))
        assert client._endpoint == GROQ_EP

        async def mock_create(**kwargs):
            seen[provider] = dict(kwargs)
            return _resp()

        client._client.chat.completions.create = mock_create
        return client

    native = await make_client("groq", GROQ_EP, 90.0)
    legacy = await make_client("openai", GROQ_EP, 91.0)
    for client in (native, legacy):
        result = await client.generate_structured(
            model=GPTOSS, prompt="p", response_schema=_S, disable_reasoning=True)
        assert result.parsed is not None
        assert result.output_mode == "json_schema"
    assert seen["groq"] == seen["openai"]
    assert "extra_body" not in seen["groq"]  # omitted before first request
    assert seen["groq"]["response_format"]["type"] == "json_schema"


# ------------------------------------------------------------------
# Capability memory: bounds, expiry, reset
# ------------------------------------------------------------------

def test_memory_bound_evicts_oldest_first(memories, monkeypatch):
    monkeypatch.setattr(Client, "_CAPABILITY_MEMORY_MAX_ENTRIES", 3)
    c = Client(_settings())
    for i in range(5):
        c._mark_tier_unsupported(f"model-{i}", "json_schema")
    assert len(Client._capability_memory_ts) <= 3
    assert len(Client._unsupported_tiers) <= 3
    # Newest findings survive; oldest were evicted.
    assert c._tier_unsupported("model-4", "json_schema")
    assert not c._tier_unsupported("model-0", "json_schema")


def test_memory_expiry_forces_reprobe(memories, monkeypatch):
    c = Client(_settings())
    c._mark_tier_unsupported("m", "json_schema")
    assert c._tier_unsupported("m", "json_schema")
    monkeypatch.setattr(Client, "_CAPABILITY_MEMORY_TTL_SECONDS", -1.0)
    assert not c._tier_unsupported("m", "json_schema")
    assert ("https://api.openai.com/v1", "m", "json_schema") not in Client._unsupported_tiers


def test_memory_reset_clears_all(memories):
    c = Client(_settings())
    c._mark_tier_unsupported("m", "json_schema")
    c._mark_reasoning_rejected("m")
    Client.reset_capability_memory()
    assert not Client._unsupported_tiers and not Client._reasoning_unsupported
    assert not Client._capability_memory_ts


# ------------------------------------------------------------------
# output_mode recording + compat fingerprint version
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_output_mode_recorded(memories):
    client = Client(_settings())

    async def mock_create(**kwargs):
        return _resp()

    client._client.chat.completions.create = mock_create
    result = await client.generate_structured(
        model="m", prompt="p", response_schema=_S)
    assert result.output_mode == "json_schema"
    plain = await client.generate_text(model="m", prompt="p")
    assert plain.output_mode == "plain"


def test_compat_version_fingerprinted(tmp_path, monkeypatch):
    import app.services.provider_capabilities as cap_mod
    from app.services.result_cache import ResultCache

    s = _settings_for_cache(tmp_path)
    base = ResultCache(s).manifest_key(file_bytes=b"d", filename="a.png", page_count=1)
    monkeypatch.setattr(cap_mod, "COMPAT_POLICY_VERSION", "compat-TEST")
    changed = ResultCache(s).manifest_key(file_bytes=b"d", filename="a.png", page_count=1)
    assert base != changed
    assert COMPAT_POLICY_VERSION.startswith("compat-v")


def _settings_for_cache(tmp_path):
    from app.core.config import Settings

    s = Settings()
    s.database_enabled = False
    s.source_storage_path = str(tmp_path / "sources")
    s.cache_path = str(tmp_path / "cache")
    s.ocr_cache_path = str(tmp_path / "cache" / "ocr-results")
    s.result_cache_enabled = True
    s.result_cache_ttl_seconds = 3600.0
    s.result_cache_max_entries = 64
    return s
