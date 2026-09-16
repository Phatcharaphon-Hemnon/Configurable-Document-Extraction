"""Application configuration loaded from backend/.env."""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[3]
API_ROOT = REPO_ROOT / "api"
load_dotenv(API_ROOT / ".env")


def runtime_path(value: str) -> str:
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else REPO_ROOT / path)


@dataclass(frozen=True)
class ProviderProfile:
    """Static connection profile for one OpenAI-compatible LLM provider."""

    base_url: str
    native_key_var: str = ""
    # "" means LLM_MODEL is required (no silent default for this provider).
    default_model: str = ""
    default_temperature: float = 0.0
    # False = chat completions has no strict json_schema tier (DeepSeek):
    # the client starts at the json_object tier instead of burning a call.
    strict_json_schema: bool = True


# All providers are OpenAI-compatible (chat.completions + Bearer auth).
# Native Anthropic/Claude is deliberately absent: its Messages API uses
# different auth headers and request bodies, so it cannot run on the
# shared client — use the openrouter/opencode gateway entries instead.
# See docs/guides/llm_providers.md for the full per-provider setup matrix.
LLM_PROVIDERS: dict[str, ProviderProfile] = {
    "openai": ProviderProfile("https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-5.4-mini"),
    "xai": ProviderProfile("https://api.x.ai/v1", "XAI_API_KEY", "grok-4"),
    "gemini": ProviderProfile(
        "https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY", "gemini-2.5-flash"
    ),
    "openrouter": ProviderProfile("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "openrouter/auto"),
    # No default model by design: LLM_MODEL is required for deepseek so a
    # missing value can never be mistaken for a recommendation.
    "deepseek": ProviderProfile("https://api.deepseek.com", "DEEPSEEK_API_KEY", strict_json_schema=False),
    # China region via LLM_BASE_URL=https://api.moonshot.cn/v1
    "kimi": ProviderProfile("https://api.moonshot.ai/v1", "MOONSHOT_API_KEY", "kimi-k2.6"),
    "ollama-cloud": ProviderProfile("https://ollama.com/v1", "OLLAMA_API_KEY", "gpt-oss:20b", 1.0),
    "ollama-local": ProviderProfile("http://localhost:11434/v1", "", "gpt-oss:20b"),
    "mistral": ProviderProfile("https://api.mistral.ai/v1", "MISTRAL_API_KEY", "mistral-large-latest"),
    # NVIDIA NIM (build.nvidia.com): OpenAI-compatible chat completions at
    # integrate.api.nvidia.com; key (nvapi-...) via Get API Key on any model page.
    # NOTE (2026-09-14): meta/llama-3.3-70b-instruct returned 410 Gone
    # (EOL 2026-08-26) — default is the measured-fastest working NVIDIA text
    # model (Router 4/4 correct at ~2s vs 90b-vision 92s timeouts; see
    # docs/reports/router_timeout_nvidia_2026-09-14.md). Override with LLM_MODEL.
    "nvidia": ProviderProfile(
        "https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY", "meta/llama-3.2-11b-vision-instruct"
    ),
    # Local OpenClaw gateway (enable gateway.http.endpoints.chatCompletions first).
    "openclaw": ProviderProfile("http://127.0.0.1:18789/v1", "OPENCLAW_API_KEY", "openclaw/default"),
    # Groq (LPU inference). Documented 2026-09-14, not live-verified as a
    # native row: strict json_schema + free limits documented for
    # openai/gpt-oss-20b (see docs/reference/provider_compatibility.md);
    # temperature 0.6 per Groq's gpt-oss guidance.
    "groq": ProviderProfile("https://api.groq.com/openai/v1", "GROQ_API_KEY", "openai/gpt-oss-20b", 0.6),
    # OpenCode Zen gateway; literal key "public" serves the free-tier models.
    "opencode": ProviderProfile("https://opencode.ai/zen/v1", "OPENCODE_API_KEY", "gpt-5.4-mini"),
    # xKiro gateway (one key, many vendor/model IDs). No default model by
    # design: pick a verified model ID explicitly (bare names 404 upstream),
    # so a missing value can never be mistaken for a recommendation.
    # Documented 2026-09-14, not live-verified; see docs/guides/ai_provider.md.
    "xkiro": ProviderProfile("https://api.xkiro.com/v1", "XKIRO_API_KEY", ""),
}


class Settings:
    def __init__(self) -> None:
        self.app_name = os.getenv("APP_NAME", "Configurable Document Extraction")
        self.app_env = os.getenv("APP_ENV", "development")
        self.app_host = os.getenv("APP_HOST", "0.0.0.0")
        self.app_port = int(os.getenv("APP_PORT", "8000"))
        self.app_debug = os.getenv("APP_DEBUG", "true").lower() == "true"
        self.frontend_origins = os.getenv("FRONTEND_ORIGINS", "http://localhost:5173")
        self.max_upload_mb = int(os.getenv("MAX_UPLOAD_MB", "10"))
        self.supported_languages = os.getenv("SUPPORTED_LANGUAGES", "en,th")
        kb = Path(os.getenv("KNOWLEDGE_BASE_PATH", "app/data/knowledge_base"))
        self.knowledge_base_path = str(kb if kb.is_absolute() else API_ROOT / kb)

        # Fixed 3-document-type system. Few-shot examples cost tokens → default OFF.
        self.few_shot_examples_per_doc_type = int(os.getenv("FEW_SHOT_EXAMPLES_PER_DOC_TYPE", "0"))

        # --- Performance / token tuning ---
        # Skip the Judge call entirely for clean extractions (100% completeness,
        # no validation errors, all field confidences >= judge_skip_confidence).
        self.judge_skip_when_clean = os.getenv("JUDGE_SKIP_WHEN_CLEAN", "true").lower() == "true"
        self.judge_skip_confidence = float(os.getenv("JUDGE_SKIP_CONFIDENCE", "0.85"))
        # Router only classifies: cap its output (reasoning models can wander
        # for thousands of tokens) and only send the top of the document.
        self.router_max_tokens = int(os.getenv("ROUTER_MAX_TOKENS", "400"))
        self.router_text_chars = int(os.getenv("ROUTER_TEXT_CHARS", "2000"))
        # Cache OCR results in memory by file hash (duplicate uploads of the
        # same document skip OCR entirely).
        self.ocr_cache_enabled = os.getenv("OCR_CACHE_ENABLED", "true").lower() == "true"
        # Persistent completed-result cache (SQLite, no Redis). Fingerprints
        # every result-affecting input; TTL 7 days; max 128 page entries.
        # Disabled only via env; force_refresh (per request) bypasses the
        # result cache while OCR caching stays on.
        self.result_cache_enabled = os.getenv("RESULT_CACHE_ENABLED", "true").lower() == "true"
        try:
            self.result_cache_ttl_seconds = float(os.getenv("RESULT_CACHE_TTL_SECONDS", str(7 * 24 * 3600)))
        except ValueError:
            self.result_cache_ttl_seconds = float(7 * 24 * 3600)
        self.result_cache_max_entries = max(1, int(os.getenv("RESULT_CACHE_MAX_ENTRIES", "128")))

        # --- AI provider: one text model for Router + Extractor + Judge ---
        # Three variables control everything — change provider/model by editing
        # LLM_PROVIDER / LLM_API_KEY / LLM_MODEL only (see docs/guides/ai_provider.md
        # for the per-provider setup matrix).
        self.llm_provider = (os.getenv("LLM_PROVIDER", "ollama-cloud").strip().lower() or "ollama-cloud")
        try:
            _profile = LLM_PROVIDERS[self.llm_provider]
        except KeyError:
            raise ValueError(
                f"Unknown LLM_PROVIDER={self.llm_provider!r} — "
                f"expected one of: {sorted(LLM_PROVIDERS)}"
            ) from None
        _native_key = os.getenv(_profile.native_key_var, "") if _profile.native_key_var else ""
        self.llm_api_key = (os.getenv("LLM_API_KEY", "") or _native_key).strip()
        self.llm_base_url = os.getenv("LLM_BASE_URL", "").strip() or _profile.base_url
        self.llm_model = os.getenv("LLM_MODEL", "").strip() or _profile.default_model
        if not self.llm_model:
            raise ValueError(
                f"LLM_MODEL is required when LLM_PROVIDER={self.llm_provider!r} "
                "(this provider ships no default model; see docs/guides/ai_provider.md)."
            )
        self.router_model_name = os.getenv("ROUTER_MODEL_NAME", "").strip() or self.llm_model
        self.judge_model_name = os.getenv("JUDGE_MODEL_NAME", "").strip() or self.llm_model
        self.extraction_model_name = os.getenv("EXTRACTION_MODEL_NAME", "").strip() or self.llm_model
        # gpt-oss reasons in Harmony format and degrades on low temperatures
        # (OpenAI recommends 1.0 for it). Other providers keep 0.0.
        _temp_default = _profile.default_temperature
        try:
            self.llm_temperature = float(os.getenv("LLM_TEMPERATURE", "") or _temp_default)
        except ValueError:
            self.llm_temperature = _temp_default
        # Reasoning-effort override for reasoning models on endpoints with a
        # documented `reasoning_effort` parameter (e.g. NVIDIA NIM gpt-oss,
        # xKiro per-model levels). Default "" preserves current behavior
        # exactly (undocumented extra_body flag only). "none" is xKiro's
        # documented explicit-off value, but support is per-model and an
        # accepted request does NOT prove reasoning was disabled (unsupported
        # levels are silently adjusted per xKiro docs). Applies solely to
        # exact (endpoint, model) pairs in the client's verified allowlist —
        # other providers/models always keep current behavior.
        _eff = os.getenv("LLM_REASONING_EFFORT", "").strip().lower()
        self.llm_reasoning_effort = _eff if _eff in ("low", "medium", "high", "none") else ""
        # Vision model is LEGACY/optional: the pipeline no longer needs it.
        # All uploads (images + PDFs) go through the configured local OCR
        # engine into the text-only pipeline using the single text model above.
        self.vision_model_name = os.getenv("VISION_MODEL_NAME", "")
        self.extraction_max_tokens = int(os.getenv("EXTRACTION_MAX_TOKENS", "8000"))
        # Single-request timeout (45s): healthy calls finish in ~2-20s, so a
        # 45s stall gets one same-tier retry (45 + 2s backoff + 45 ≈ 92s)
        # inside the 100s Router/Judge limits. Shorter limits bound failures;
        # they do not accelerate model generation.
        self.llm_request_timeout_seconds = float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "45"))
        self.llm_max_concurrent_requests = int(os.getenv("LLM_MAX_CONCURRENT_REQUESTS", "1"))
        if self.llm_max_concurrent_requests < 1:
            raise ValueError("LLM_MAX_CONCURRENT_REQUESTS must be at least 1")
        # Default comes from the provider profile: deepseek's chat completions
        # only offer json_object, so strict mode is off there unless the env
        # var explicitly says otherwise (see api/scripts/time_gateway_modes.py).
        _strict_raw = os.getenv("DISABLE_STRICT_JSON_SCHEMA", "").strip().lower()
        if _strict_raw in ("true", "1", "yes"):
            self.disable_strict_json_schema = True
        elif _strict_raw in ("false", "0", "no"):
            self.disable_strict_json_schema = False
        else:
            self.disable_strict_json_schema = not _profile.strict_json_schema

        # --- Document parsing (local OCR — no API key, no network) ---
        # OCR_ENGINE: "tesseract" (default, eng+tha) | "rapidocr" (PP-OCRv5 TH)
        #           | "hybrid" (opt-in: PP-OCRv5 TH→EN retry + TrOCR handwriting).
        # Tesseract stays the default while hybrid is benchmarked.
        # OCR_DPI controls PDF render resolution (150–600, default 300).
        self.ocr_dpi = int(os.getenv("OCR_DPI", "300"))
        self.cache_path = runtime_path(os.getenv("PROJECT_CACHE_DIR", ".cache"))
        self.ocr_cache_path = str(Path(self.cache_path) / "ocr-results")
        self.ocr_cache_max_files = max(1, int(os.getenv("OCR_CACHE_MAX_FILES", "128")))
        self.ocr_engine = os.getenv("OCR_ENGINE", "tesseract").strip().lower() or "tesseract"
        self.ocr_languages = os.getenv("OCR_LANGUAGES", "eng+tha")
        local_binary = REPO_ROOT / ".local/ocr/usr/bin/tesseract"
        self.tesseract_cmd = os.getenv("TESSERACT_CMD", str(local_binary) if local_binary.exists() else "tesseract")
        local_models = REPO_ROOT / ".local/ocr/usr/share/tessdata"
        self.tessdata_dir = os.getenv("TESSDATA_DIR", str(local_models) if local_models.exists() else "")

        # --- Hybrid OCR (opt-in; see docs/guides/thai_catalog_hybrid_ocr.md) ---
        # Selective TrOCR retry for uncertain English line crops only.
        # Threshold: RapidOCR confidence below this is eligible.
        # Limit: at most N lowest-confidence eligible regions per page, CPU sequential.
        try:
            self.hybrid_trocr_conf_threshold = float(os.getenv("HYBRID_TROCR_CONF_THRESHOLD", "0.80"))
        except ValueError:
            self.hybrid_trocr_conf_threshold = 0.80
        self.hybrid_trocr_max_regions = max(0, int(os.getenv("HYBRID_TROCR_MAX_REGIONS", "10")))
        self.hybrid_trocr_model = os.getenv(
            "HYBRID_TROCR_MODEL", "microsoft/trocr-base-handwritten").strip() or "microsoft/trocr-base-handwritten"
        self.hybrid_trocr_revision = os.getenv(
            "HYBRID_TROCR_REVISION", "aff187bd81f8d73231cd3ed24b7857fcb10ae00e").strip()
        # Preprocessing / model fingerprint version (bump when logic changes).
        self.hybrid_preprocess_version = os.getenv("HYBRID_PREPROCESS_VERSION", "hybrid-v1")

        # --- Langfuse (optional; disabled when keys are missing) ---
        self.langfuse_public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "")
        self.langfuse_secret_key = os.getenv("LANGFUSE_SECRET_KEY", "")
        self.langfuse_host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

        # --- Temporal (optional; in-process pipeline when disabled) ---
        self.temporal_enabled = os.getenv("TEMPORAL_ENABLED", "false").lower() == "true"
        self.temporal_address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
        self.temporal_task_queue = os.getenv("TEMPORAL_TASK_QUEUE", "doc-extraction")

        # --- Guardrails Configuration ---
        # Master switch for all guards (set false to disable in development)
        self.guards_enabled = os.getenv("GUARDS_ENABLED", "true").lower() == "true"

        # Rate limiting
        self.rate_limit_max_requests = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "100"))
        self.rate_limit_window_seconds = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
        self.rate_limit_max_concurrent = int(os.getenv("RATE_LIMIT_MAX_CONCURRENT", "5"))

        # Timeout configuration (seconds).
        # Stage limits sit ABOVE the worst single LLM call (request timeout +
        # one same-tier retry ≈ 92s at the 45s default): healthy and recovering
        # calls pass, while a fully stalled stage fails fast instead of hanging
        # for minutes. Shorter limits bound failures; they do not accelerate
        # model generation. A request timeout above a stage limit leaves no
        # room for the timeout retry — startup warns (see validate_timeout_config).
        self.router_timeout_seconds = float(os.getenv("ROUTER_TIMEOUT_SECONDS", "100"))
        self.extractor_timeout_seconds = float(os.getenv("EXTRACTOR_TIMEOUT_SECONDS", "150"))
        self.judge_timeout_seconds = float(os.getenv("JUDGE_TIMEOUT_SECONDS", "100"))
        self.ocr_timeout_seconds = float(os.getenv("OCR_TIMEOUT_SECONDS", "120"))

        # Content limits
        self.max_document_chars = int(os.getenv("MAX_DOCUMENT_CHARS", "50000"))
        self.max_prompt_chars = int(os.getenv("MAX_PROMPT_CHARS", "12000"))

        # PII detection
        self.pii_detection_enabled = os.getenv("PII_DETECTION_ENABLED", "true").lower() == "true"
        self.pii_redact_in_logs = os.getenv("PII_REDACT_IN_LOGS", "true").lower() == "true"

        # Audit logging
        self.audit_log_enabled = os.getenv("AUDIT_LOG_ENABLED", "true").lower() == "true"
        self.audit_log_file = runtime_path(os.getenv("AUDIT_LOG_FILE", "data/logs/security_audit.jsonl"))

        # --- Database Configuration ---
        self.database_enabled = os.getenv("DATABASE_ENABLED", "true").lower() == "true"
        self.database_path = runtime_path(os.getenv("DATABASE_PATH", "data/extraction.db"))
        self.source_storage_path = runtime_path(os.getenv("SOURCE_STORAGE_PATH", "data/sources"))

    @property
    def frontend_origin_list(self) -> list[str]:
        return [item.strip() for item in self.frontend_origins.split(",") if item.strip()]

    @property
    def cors_origins(self) -> list[str]:
        defaults = [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "https://configurable-document-extraction.vercel.app",
            "https://configurable-document-extraction-git-makefrontend-peter-o-manufactor.vercel.app",
        ]
        return list(dict.fromkeys([*defaults, *self.frontend_origin_list]))

    @property
    def supported_language_list(self) -> list[str]:
        return [item.strip() for item in self.supported_languages.split(",") if item.strip()]

    @property
    def knowledge_base_directory(self) -> Path:
        return Path(self.knowledge_base_path)

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


def validate_timeout_config(settings: Settings) -> list[str]:
    """Warn when the request timeout leaves no room for a timeout retry.

    One timeout retry costs ~ (request + 2s backoff + request). When the
    request timeout exceeds (or nearly fills) a stage limit, the stage
    cancels before the retry can run — the observed 200s-Router vs 1000s-
    request failure. Returns warning strings (callers log them at startup).
    No document content or credentials are included.
    """
    import logging as _logging

    warnings: list[str] = []
    request_timeout = float(getattr(settings, "llm_request_timeout_seconds", 45) or 45)
    stages = {
        "router": float(getattr(settings, "router_timeout_seconds", 100) or 100),
        "extractor": float(getattr(settings, "extractor_timeout_seconds", 150) or 150),
        "judge": float(getattr(settings, "judge_timeout_seconds", 100) or 100),
    }
    # One retry needs ~2x request + 2s backoff inside the stage limit.
    retry_floor = 2 * request_timeout + 2.0
    for stage, limit in stages.items():
        if request_timeout > limit:
            warnings.append(
                f"LLM_REQUEST_TIMEOUT_SECONDS={request_timeout:g}s exceeds "
                f"{stage.upper()}_TIMEOUT_SECONDS={limit:g}s — the stage cancels "
                "before a timeout retry can run"
            )
        elif retry_floor > limit:
            warnings.append(
                f"LLM_REQUEST_TIMEOUT_SECONDS={request_timeout:g}s leaves no room for "
                f"one timeout retry in {stage} limit {limit:g}s "
                f"(needs ~{retry_floor:g}s); single attempts still run"
            )
    for warning in warnings:
        _logging.getLogger(__name__).warning(warning)
    return warnings


@lru_cache
def get_settings() -> Settings:
    return Settings()
