from dataclasses import dataclass

from app.core.config import Settings
from app.schemas.documents import DocumentLanguage, DocumentType


@dataclass(slots=True)
class RoutingDecision:
    doc_type: DocumentType
    language: DocumentLanguage
    reason: str


class RouterAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def classify(self, filename: str, content_type: str | None = None, text_hint: str | None = None) -> RoutingDecision:
        lower_name = filename.lower()
        lower_text = text_hint.lower() if text_hint is not None else ""

        if self._contains_any(lower_name, ["invoice"]) or self._contains_any(lower_text, ["invoice", "tax invoice", "billing"]):
            return RoutingDecision(DocumentType.INVOICE, DocumentLanguage.EN, "Matched invoice pattern from filename or OCR text")
        if self._contains_any(lower_name, ["po", "purchase"]) or self._contains_any(lower_text, ["po no", "purchase order", "purchase no"]):
            return RoutingDecision(DocumentType.PURCHASE_ORDER, DocumentLanguage.EN, "Matched purchase order pattern from filename or OCR text")
        if self._contains_any(lower_name, ["delivery", "note"]) or self._contains_any(lower_text, ["delivery note", "delivered by", "delivery date"]):
            return RoutingDecision(DocumentType.DELIVERY_NOTE, DocumentLanguage.EN, "Matched delivery note pattern from filename or OCR text")

        supported = ", ".join(self.settings.supported_doc_type_list)
        raise ValueError(f"Unsupported document. Supported doc types: {supported}. Content-Type: {content_type or 'unknown'}")

    def _contains_any(self, source: str, tokens: list[str]) -> bool:
        return any(token in source for token in tokens)
