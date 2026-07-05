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
        self.few_shot_examples_per_doc_type = int(os.getenv("FEW_SHOT_EXAMPLES_PER_DOC_TYPE", "5"))
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", os.getenv("API_KEY", "test-api-key"))
        self.judge_model_name = os.getenv("JUDGE_MODEL_NAME", "gemini-2.5-pro")
        # Recommended model used for extraction
        self.recommended_extraction_model_name = os.getenv("RECOMMENDED_EXTRACTION_MODEL_NAME", "gemini-2.5-pro")
        self.recommended_extraction_model_display_name = os.getenv("RECOMMENDED_EXTRACTION_MODEL_DISPLAY_NAME", "Gemini 2.5 Pro")
        self.recommended_extraction_model_reason = os.getenv(
            "RECOMMENDED_EXTRACTION_MODEL_REASON",
            "Best available model for complex document extraction and reasoning.",
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
