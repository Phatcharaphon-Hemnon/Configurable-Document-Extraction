from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import Settings
from app.schemas.documents import DocumentLanguage, FieldDefinition
from app.schemas.gemini_schemas import RoutingResponseSchema
from app.services.sut_genai_client import SutGenAICallError as GeminiCallError, SutGenAIClient as GeminiClient


@dataclass(slots=True)
class RoutingDecision:
    doc_type: str
    language: DocumentLanguage
    reason: str
    confidence: float = 1.0
    suggested_fields: list[FieldDefinition] = field(default_factory=list)


class RouterAgent:
    """Open-schema router. Does NOT restrict documents to a fixed type list —
    it proposes whatever document type it recognizes and, per document, a
    list of fields it expects to find (with a likely_required flag), which
    the Extractor and Validator then use instead of a hardcoded catalog.
    """

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
        prompt_parts = [
            "You are a document classification router with an OPEN, unrestricted schema.",
            "Look at this document and identify what TYPE of document it is, in your own words "
            "(e.g. 'invoice', 'purchase_order', 'delivery_note', 'receipt', 'medical_form', "
            "'id_card', or any other type you recognize — do not limit yourself to a fixed list).",
            "Also detect the primary language: en (English), th (Thai), or other.",
            "Propose a list of fields you would expect to find on a document of this type, based "
            "on what you can actually see. For each suggested field, give a short name, a brief "
            "description, and mark likely_required=true only if the document would be essentially "
            "useless/unidentifiable without that field (e.g. an invoice number, a total amount). "
            "Most fields should be likely_required=false.",
            "Base your decision on the actual document content — not the filename.",
            "Return your confidence (0.0–1.0) reflecting how certain you are.",
            f"Filename (metadata only, do not rely on it): {filename}",
        ]
        if text_hint and text_hint.strip():
            prompt_parts.append(f"Document text hint:\n{text_hint.strip()}")

        if image_bytes is not None:
            prompt_parts.append("Analyze the attached document image to determine its type, language, and fields.")
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

        suggested = [
            FieldDefinition(
                name=f.name,
                description=f.description,
                likely_required=f.likely_required,
            )
            for f in parsed.suggested_fields
        ]

        return RoutingDecision(
            doc_type=parsed.doc_type,
            language=parsed.language,
            reason=parsed.reason,
            confidence=parsed.confidence,
            suggested_fields=suggested,
        )