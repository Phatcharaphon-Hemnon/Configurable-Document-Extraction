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
        self.supported_doc_types = os.getenv("SUPPORTED_DOC_TYPES", "invoice,po,delivery_note")
        self.knowledge_base_path = os.getenv("KNOWLEDGE_BASE_PATH", "app/data/knowledge_base")
        # Schema mode: "strict" enforces the original 3-document-type spec with
        # blocking validation; "open" allows free-form document types with soft
        # validation.  Default is "strict" to match the original assignment spec.
        _schema_mode = os.getenv("SCHEMA_MODE", "strict").strip().lower()
        if _schema_mode not in ("strict", "open"):
            raise ValueError(
                f"SCHEMA_MODE must be 'strict' or 'open', got {_schema_mode!r}"
            )
        self.schema_mode: str = _schema_mode
        self.few_shot_examples_per_doc_type = int(os.getenv("FEW_SHOT_EXAMPLES_PER_DOC_TYPE", "5"))
        # NVIDIA API Key (primary LLM provider)
        self.nvidia_api_key = (os.getenv("NVIDIA_API_KEY") or os.getenv("OPENROUTER_API_KEY") or "").strip()
        self.openrouter_api_key = self.nvidia_api_key  # backwards-compatible alias
        # Llama Cloud API Key for LlamaParse document parsing
        self.llama_cloud_api_key = os.getenv("LLAMA_CLOUD_API_KEY", "")
        # Per-attempt timeout (seconds) for LLM requests.
        # Vision models can be slow; 90s gives headroom without hanging forever.
        self.llm_request_timeout_seconds = float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "90"))

        self.disable_strict_json_schema = os.getenv("DISABLE_STRICT_JSON_SCHEMA", "false").lower() == "true"

        self.router_model_name = os.getenv("ROUTER_MODEL_NAME", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning")
        self.judge_model_name = os.getenv("JUDGE_MODEL_NAME", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning")
        
        self.recommended_extraction_model_name = os.getenv("RECOMMENDED_EXTRACTION_MODEL_NAME", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning")
        
        self.recommended_extraction_model_display_name = os.getenv("RECOMMENDED_EXTRACTION_MODEL_DISPLAY_NAME", "NVIDIA Nemotron 3 Nano Omni (NVIDIA API, reasoning)")
        self.recommended_extraction_model_reason = os.getenv(
            "RECOMMENDED_EXTRACTION_MODEL_REASON",
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning is a reasoning-capable multimodal model via NVIDIA NIM API. Reasoning is disabled for structured-output pipeline calls.",
        )

        # Extraction-specific max_tokens — bumped from 4096 to 8000 to accommodate
        # reasoning-token overhead from the nemotron reasoning model.
        self.extraction_max_tokens = int(os.getenv("EXTRACTION_MAX_TOKENS", "8000"))

    @property
    def supported_doc_type_list(self) -> list[str]:
        return [item.strip() for item in self.supported_doc_types.split(",") if item.strip()]

    @property
    def supported_language_list(self) -> list[str]:
        return [item.strip() for item in self.supported_languages.split(",") if item.strip()]

    @property
    def frontend_origin_list(self) -> list[str]:
        return [item.strip() for item in self.frontend_origins.split(",") if item.strip()]

    @property
    def knowledge_base_directory(self) -> Path:
        return Path(self.knowledge_base_path)


@lru_cache
def get_settings() -> Settings:
    return Settings()
