from __future__ import annotations

from dataclasses import dataclass

from Backend.app.core.config import Settings
from Backend.app.schemas.documents import DocumentLanguage, DocumentType
from Backend.app.schemas.gemini_schemas import RoutingResponseSchema
from Backend.app.services.sut_genai_client import SutGenAICallError as GeminiCallError, SutGenAIClient as GeminiClient


@dataclass(slots=True)
class RoutingDecision:
    doc_type: DocumentType
    language: DocumentLanguage
    reason: str
    confidence: float = 1.0


class RouterAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = GeminiClient(settings)

    def classify(
        self,
        filename: str,
        content_type: str | None = None,
        text_hint: str | None = None,
        image_bytes: bytes | None = None,
        image_mime_type: str | None = None,
    ) -> RoutingDecision:
        supported = ", ".join(self.settings.supported_doc_type_list)
        prompt_parts = [
            "You are a document classification router.",
            f"Classify this document into exactly one of: {supported}.",
            "Also detect the primary language: en (English) or th (Thai).",
            "Base your decision on the actual document content — not the filename.",
            "Return your confidence (0.0–1.0) reflecting how certain you are.",
            f"Filename (metadata only, do not rely on it): {filename}",
        ]
        if text_hint and text_hint.strip():
            prompt_parts.append(f"Document text hint:\n{text_hint.strip()}")
        
        if image_bytes is not None:
            prompt_parts.append("Analyze the attached document image to determine its type and language.")
        elif not (text_hint and text_hint.strip()):
            raise GeminiCallError(
                "Router requires either document text or an image to classify the document"
            )

        prompt = "\n\n".join(prompt_parts)

        try:
            result = self._client.generate_structured(
                model=self.settings.router_model_name,
                prompt=prompt,
                response_schema=RoutingResponseSchema,
                image_bytes=image_bytes,
                image_mime_type=image_mime_type or content_type,
            )
        except GeminiCallError:
            raise
        except Exception as exc:
            raise GeminiCallError(f"Router classification failed: {exc}") from exc

        parsed = result.parsed
        assert isinstance(parsed, RoutingResponseSchema)

        return RoutingDecision(
            doc_type=parsed.doc_type,
            language=parsed.language,
            reason=parsed.reason,
            confidence=parsed.confidence,
        )
