"""Tests for the LLM_PROVIDER / LLM_API_KEY / LLM_MODEL variable scheme."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import BaseModel

_REPO_ROOT = Path(__file__).resolve().parents[2]
_API_ROOT = Path(__file__).resolve().parents[1]
for _p in (_REPO_ROOT, _API_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.core.config import LLM_PROVIDERS, Settings  # noqa: E402
from app.services.client import _build_json_prompt_suffix  # noqa: E402

_VARS = (
    "LLM_PROVIDER",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_BASE_URL",
    "LLM_TEMPERATURE",
    "DISABLE_STRICT_JSON_SCHEMA",
    "ROUTER_MODEL_NAME",
    "EXTRACTION_MODEL_NAME",
    "JUDGE_MODEL_NAME",
    "OPENAI_API_KEY",
    "XAI_API_KEY",
    "GEMINI_API_KEY",
    "OPENROUTER_API_KEY",
    "DEEPSEEK_API_KEY",
    "MOONSHOT_API_KEY",
    "OLLAMA_API_KEY",
    "MISTRAL_API_KEY",
    "OPENCLAW_API_KEY",
    "OPENCODE_API_KEY",
)

# provider -> (base_url, native_key_var, default_model or None, temperature, strict_disabled_by_default)
EXPECTED_PROVIDERS: dict[str, tuple[str, str, str | None, float, bool]] = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-5.4-mini", 0.0, False),
    "xai": ("https://api.x.ai/v1", "XAI_API_KEY", "grok-4", 0.0, False),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY", "gemini-2.5-flash", 0.0, False),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "openrouter/auto", 0.0, False),
    "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY", None, 0.0, True),
    "kimi": ("https://api.moonshot.ai/v1", "MOONSHOT_API_KEY", "kimi-k2.6", 0.0, False),
    "ollama-cloud": ("https://ollama.com/v1", "OLLAMA_API_KEY", "gpt-oss:20b", 1.0, False),
    "ollama-local": ("http://localhost:11434/v1", "", "gpt-oss:20b", 0.0, False),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY", "mistral-large-latest", 0.0, False),
    "openclaw": ("http://127.0.0.1:18789/v1", "OPENCLAW_API_KEY", "openclaw/default", 0.0, False),
    "opencode": ("https://opencode.ai/zen/v1", "OPENCODE_API_KEY", "gpt-5.4-mini", 0.0, False),
}


@pytest.fixture
def clean_env(monkeypatch):
    for var in _VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_registry_matches_expected_set():
    assert set(LLM_PROVIDERS) == set(EXPECTED_PROVIDERS)


@pytest.mark.parametrize("provider", sorted(EXPECTED_PROVIDERS))
def test_each_provider_resolves(clean_env, provider):
    base_url, _native_var, default_model, temperature, strict_disabled = EXPECTED_PROVIDERS[provider]
    clean_env.setenv("LLM_PROVIDER", provider)
    if default_model is None:
        clean_env.setenv("LLM_MODEL", "explicit-model")
        expected_model = "explicit-model"
    else:
        expected_model = default_model
    settings = Settings()
    assert settings.llm_base_url == base_url
    assert settings.llm_model == expected_model
    assert settings.router_model_name == expected_model
    assert settings.extraction_model_name == expected_model
    assert settings.judge_model_name == expected_model
    assert settings.llm_temperature == temperature
    assert settings.disable_strict_json_schema is strict_disabled


@pytest.mark.parametrize(
    "provider", sorted(p for p, row in EXPECTED_PROVIDERS.items() if row[1])
)
def test_native_key_fallback_all_providers(clean_env, provider):
    _base_url, native_var, default_model, _temp, _strict = EXPECTED_PROVIDERS[provider]
    clean_env.setenv("LLM_PROVIDER", provider)
    clean_env.setenv(native_var, "native-test-key")
    if default_model is None:
        clean_env.setenv("LLM_MODEL", "explicit-model")
    assert Settings().llm_api_key == "native-test-key"


def test_branch_defaults_are_ollama_cloud(clean_env):
    settings = Settings()
    assert settings.llm_provider == "ollama-cloud"
    assert settings.llm_base_url == "https://ollama.com/v1"
    assert settings.llm_model == "gpt-oss:20b"
    assert settings.router_model_name == "gpt-oss:20b"
    assert settings.extraction_model_name == "gpt-oss:20b"
    assert settings.judge_model_name == "gpt-oss:20b"
    assert settings.llm_temperature == 1.0


def test_single_model_variable_flows_to_all_stages(clean_env):
    clean_env.setenv("LLM_MODEL", "qwen2.5:1.5b")
    settings = Settings()
    assert settings.router_model_name == "qwen2.5:1.5b"
    assert settings.extraction_model_name == "qwen2.5:1.5b"
    assert settings.judge_model_name == "qwen2.5:1.5b"


def test_per_stage_override_still_wins(clean_env):
    clean_env.setenv("LLM_MODEL", "gpt-oss:20b")
    clean_env.setenv("ROUTER_MODEL_NAME", "qwen2.5:1.5b")
    settings = Settings()
    assert settings.router_model_name == "qwen2.5:1.5b"
    assert settings.extraction_model_name == "gpt-oss:20b"


def test_openai_profile(clean_env):
    clean_env.setenv("LLM_PROVIDER", "openai")
    clean_env.setenv("LLM_API_KEY", "sk-test")
    clean_env.setenv("LLM_MODEL", "gpt-5.4-mini")
    settings = Settings()
    assert settings.llm_base_url == "https://api.openai.com/v1"
    assert settings.llm_api_key == "sk-test"
    assert settings.llm_temperature == 0.0


def test_native_key_fallback(clean_env):
    clean_env.setenv("LLM_PROVIDER", "ollama-cloud")
    clean_env.setenv("OLLAMA_API_KEY", "ollama-test-key")
    assert Settings().llm_api_key == "ollama-test-key"


def test_explicit_key_wins_over_native(clean_env):
    clean_env.setenv("LLM_PROVIDER", "openai")
    clean_env.setenv("LLM_API_KEY", "explicit")
    clean_env.setenv("OPENAI_API_KEY", "native")
    assert Settings().llm_api_key == "explicit"


def test_base_url_override(clean_env):
    clean_env.setenv("LLM_BASE_URL", "http://proxy:4000/v1")
    assert Settings().llm_base_url == "http://proxy:4000/v1"


def test_deepseek_requires_model(clean_env):
    clean_env.setenv("LLM_PROVIDER", "deepseek")
    with pytest.raises(ValueError, match="LLM_MODEL is required"):
        Settings()


def test_deepseek_explicit_model_ok(clean_env):
    clean_env.setenv("LLM_PROVIDER", "deepseek")
    clean_env.setenv("LLM_MODEL", "deepseek-v4-flash")
    assert Settings().llm_model == "deepseek-v4-flash"


def test_strict_env_override_wins_over_provider_default(clean_env):
    clean_env.setenv("LLM_PROVIDER", "deepseek")
    clean_env.setenv("LLM_MODEL", "deepseek-v4-flash")
    clean_env.setenv("DISABLE_STRICT_JSON_SCHEMA", "false")
    assert Settings().disable_strict_json_schema is False
    clean_env.setenv("LLM_PROVIDER", "openai")
    clean_env.setenv("DISABLE_STRICT_JSON_SCHEMA", "true")
    assert Settings().disable_strict_json_schema is True


def test_unknown_provider_fails_fast(clean_env):
    # Native Claude/Anthropic is unsupported (Messages API is not
    # OpenAI-compatible) — it must fail here, reachable via gateways only.
    clean_env.setenv("LLM_PROVIDER", "claude")
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        Settings()


@pytest.mark.parametrize(
    "provider,model",
    [
        ("opencode", "nemotron-3.5-lightning-free"),
        ("opencode", "gpt-5.4-mini"),
        ("openrouter", "anthropic/claude-sonnet-4"),
        ("openai", "my-custom-finetune-v3"),
        ("kimi", "moonshot-v1-8k"),
    ],
)
def test_arbitrary_model_ids_pass_through(clean_env, provider, model):
    # Model names are never validated against an allowlist: any provider
    # model ID (or fine-tune) must flow to all stages untouched.
    clean_env.setenv("LLM_PROVIDER", provider)
    clean_env.setenv("LLM_MODEL", model)
    settings = Settings()
    assert settings.llm_model == model
    assert settings.router_model_name == model
    assert settings.extraction_model_name == model
    assert settings.judge_model_name == model


def test_custom_model_with_base_url_override(clean_env):
    clean_env.setenv("LLM_PROVIDER", "openai")
    clean_env.setenv("LLM_BASE_URL", "https://my-gateway.internal/v1")
    clean_env.setenv("LLM_MODEL", "my-gateway-model")
    settings = Settings()
    assert settings.llm_base_url == "https://my-gateway.internal/v1"
    assert settings.llm_model == "my-gateway-model"


def test_temperature_override(clean_env):
    clean_env.setenv("LLM_TEMPERATURE", "0.2")
    assert Settings().llm_temperature == 0.2


def test_bad_temperature_falls_back_to_default(clean_env):
    clean_env.setenv("LLM_TEMPERATURE", "not-a-number")
    assert Settings().llm_temperature == 1.0


class _DummySchema(BaseModel):
    name: str


def test_json_prompt_suffix_contains_json_word():
    # DeepSeek rejects response_format=json_object unless the prompt contains
    # the word "json" — pin that the shared suffix always does.
    assert "json" in _build_json_prompt_suffix(_DummySchema).lower()
