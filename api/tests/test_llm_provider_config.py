"""Tests for the LLM_PROVIDER / LLM_API_KEY / LLM_MODEL variable scheme."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_API_ROOT = Path(__file__).resolve().parents[1]
for _p in (_REPO_ROOT, _API_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.core.config import Settings  # noqa: E402

_VARS = (
    "LLM_PROVIDER",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_BASE_URL",
    "LLM_TEMPERATURE",
    "ROUTER_MODEL_NAME",
    "EXTRACTION_MODEL_NAME",
    "JUDGE_MODEL_NAME",
    "OPENAI_API_KEY",
    "OLLAMA_API_KEY",
)


@pytest.fixture
def clean_env(monkeypatch):
    for var in _VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


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


def test_unknown_provider_fails_fast(clean_env):
    clean_env.setenv("LLM_PROVIDER", "gemini")
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        Settings()


def test_temperature_override(clean_env):
    clean_env.setenv("LLM_TEMPERATURE", "0.2")
    assert Settings().llm_temperature == 0.2


def test_bad_temperature_falls_back_to_default(clean_env):
    clean_env.setenv("LLM_TEMPERATURE", "not-a-number")
    assert Settings().llm_temperature == 1.0
