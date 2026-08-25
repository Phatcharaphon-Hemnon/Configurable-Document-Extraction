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

        # --- AI provider: OpenCode Zen (https://opencode.ai/zen) — OpenAI-compatible ---
        self.opencode_api_key = (
            os.getenv("OPENCODE_API_KEY")
            or os.getenv("NVIDIA_API_KEY")  # legacy fallbacks
            or os.getenv("OPENROUTER_API_KEY")
            or ""
        ).strip()
        self.nvidia_api_key = self.opencode_api_key  # backwards-compatible alias
        self.openrouter_api_key = self.opencode_api_key  # backwards-compatible alias
        _DEFAULT_MODEL = "nemotron-3.5-lightning-free"
        self.router_model_name = os.getenv("ROUTER_MODEL_NAME", _DEFAULT_MODEL)
        self.judge_model_name = os.getenv("JUDGE_MODEL_NAME", _DEFAULT_MODEL)
        self.extraction_model_name = os.getenv(
            "EXTRACTION_MODEL_NAME",
            os.getenv("RECOMMENDED_EXTRACTION_MODEL_NAME", _DEFAULT_MODEL),
        )
        # Vision model for image uploads. Default EMPTY: no free model on the
        # OpenCode Zen gateway can actually read images (the gateway silently
        # strips image parts), so images go through LlamaParse OCR instead.
        # Set e.g. VISION_MODEL_NAME=<model> ONLY if you have a vision-capable model.
        self.vision_model_name = os.getenv("VISION_MODEL_NAME", "")
        self.extraction_max_tokens = int(os.getenv("EXTRACTION_MAX_TOKENS", "8000"))
        self.llm_request_timeout_seconds = float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "90"))
        self.disable_strict_json_schema = os.getenv("DISABLE_STRICT_JSON_SCHEMA", "false").lower() == "true"

        # --- Document parsing ---
        self.llama_cloud_api_key = os.getenv("LLAMA_CLOUD_API_KEY", "")

        # --- Langfuse (optional; disabled when keys are missing) ---
        self.langfuse_public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "")
        self.langfuse_secret_key = os.getenv("LANGFUSE_SECRET_KEY", "")
        self.langfuse_host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

        # --- Temporal (optional; in-process pipeline when disabled) ---
        self.temporal_enabled = os.getenv("TEMPORAL_ENABLED", "false").lower() == "true"
        self.temporal_address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
        self.temporal_task_queue = os.getenv("TEMPORAL_TASK_QUEUE", "doc-extraction")

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
