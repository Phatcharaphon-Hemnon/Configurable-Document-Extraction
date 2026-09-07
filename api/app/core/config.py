"""Application configuration loaded from backend/.env."""

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


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
        self.knowledge_base_path = os.getenv("KNOWLEDGE_BASE_PATH", "app/data/knowledge_base")

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

        # --- AI provider: one text model for Router + Extractor + Judge ---
        # Three variables control everything — change provider/model by editing
        # LLM_PROVIDER / LLM_API_KEY / LLM_MODEL only.
        self.llm_provider = (os.getenv("LLM_PROVIDER", "ollama-cloud").strip().lower() or "ollama-cloud")
        _PROVIDER_BASE_URLS = {
            "openai": "https://api.openai.com/v1",
            "ollama-cloud": "https://ollama.com/v1",
            "ollama-local": "http://localhost:11434/v1",
        }
        if self.llm_provider not in _PROVIDER_BASE_URLS:
            raise ValueError(
                f"Unknown LLM_PROVIDER={self.llm_provider!r} — "
                f"expected one of: {sorted(_PROVIDER_BASE_URLS)}"
            )
        _NATIVE_KEY_VARS = {"openai": "OPENAI_API_KEY", "ollama-cloud": "OLLAMA_API_KEY", "ollama-local": ""}
        _native_key_var = _NATIVE_KEY_VARS[self.llm_provider]
        _native_key = os.getenv(_native_key_var, "") if _native_key_var else ""
        self.llm_api_key = (os.getenv("LLM_API_KEY", "") or _native_key).strip()
        self.llm_base_url = os.getenv("LLM_BASE_URL", "").strip() or _PROVIDER_BASE_URLS[self.llm_provider]
        self.llm_model = os.getenv("LLM_MODEL", "gpt-oss:20b").strip() or "gpt-oss:20b"
        self.router_model_name = os.getenv("ROUTER_MODEL_NAME", "").strip() or self.llm_model
        self.judge_model_name = os.getenv("JUDGE_MODEL_NAME", "").strip() or self.llm_model
        self.extraction_model_name = os.getenv("EXTRACTION_MODEL_NAME", "").strip() or self.llm_model
        # gpt-oss reasons in Harmony format and degrades on low temperatures
        # (OpenAI recommends 1.0 for it). Other providers keep 0.0.
        _temp_default = 1.0 if self.llm_provider == "ollama-cloud" else 0.0
        try:
            self.llm_temperature = float(os.getenv("LLM_TEMPERATURE", "") or _temp_default)
        except ValueError:
            self.llm_temperature = _temp_default
        # Vision model is LEGACY/optional: the pipeline no longer needs it.
        # All uploads (images + PDFs) go through local RapidOCR into the
        # text-only pipeline using the single text model above.
        self.vision_model_name = os.getenv("VISION_MODEL_NAME", "")
        self.extraction_max_tokens = int(os.getenv("EXTRACTION_MAX_TOKENS", "8000"))
        self.llm_request_timeout_seconds = float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "90"))
        self.llm_max_concurrent_requests = int(os.getenv("LLM_MAX_CONCURRENT_REQUESTS", "1"))
        if self.llm_max_concurrent_requests < 1:
            raise ValueError("LLM_MAX_CONCURRENT_REQUESTS must be at least 1")
        self.disable_strict_json_schema = os.getenv("DISABLE_STRICT_JSON_SCHEMA", "false").lower() == "true"

        # --- Document parsing (local RapidOCR — no API key, no network) ---
        # OCR_DPI controls PDF render resolution (150–600, default 300).
        self.ocr_dpi = int(os.getenv("OCR_DPI", "300"))

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
        # one same-tier retry ≈ 92s): healthy and recovering calls pass, while
        # a fully stalled stage fails fast instead of hanging for minutes.
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
        self.audit_log_file = os.getenv("AUDIT_LOG_FILE", "logs/security_audit.jsonl")

        # --- Database Configuration ---
        self.database_enabled = os.getenv("DATABASE_ENABLED", "true").lower() == "true"
        self.database_path = os.getenv("DATABASE_PATH", "data/extraction.db")

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
