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
        # GitHub Models Token (primary LLM provider)
        self.github_models_token = os.getenv("GITHUB_MODELS_TOKEN", "")
        # Llama Cloud API Key for LlamaParse document parsing
        self.llama_cloud_api_key = os.getenv("LLAMA_CLOUD_API_KEY", "")

        self.router_model_name = os.getenv("ROUTER_MODEL_NAME", "gpt-4.1")
        self.judge_model_name = os.getenv("JUDGE_MODEL_NAME", "gpt-4.1")
        # Recommended model used for extraction
        self.recommended_extraction_model_name = os.getenv("RECOMMENDED_EXTRACTION_MODEL_NAME", "gpt-4.1")
        # NOTE: display name must match the actual model name above — update both together
        self.recommended_extraction_model_display_name = os.getenv("RECOMMENDED_EXTRACTION_MODEL_DISPLAY_NAME", "GPT-4.1 (GitHub Models)")
        self.recommended_extraction_model_reason = os.getenv(
            "RECOMMENDED_EXTRACTION_MODEL_REASON",
            "gpt-4.1 is free under GitHub Student Pack / Copilot Pro and not metered against premium request quota.",
        )

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
