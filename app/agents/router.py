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

    def classify(self, filename: str, content_type: str | None = None) -> RoutingDecision:
        lower_name = filename.lower()

        if "invoice" in lower_name:
            return RoutingDecision(DocumentType.INVOICE, DocumentLanguage.EN, "Filename matched invoice pattern")
        if "po" in lower_name or "purchase" in lower_name:
            return RoutingDecision(DocumentType.PURCHASE_ORDER, DocumentLanguage.EN, "Filename matched purchase order pattern")
        if "delivery" in lower_name or "note" in lower_name:
            return RoutingDecision(DocumentType.DELIVERY_NOTE, DocumentLanguage.EN, "Filename matched delivery note pattern")

        supported = ", ".join(self.settings.supported_doc_type_list)
        raise ValueError(f"Unsupported document. Supported doc types: {supported}. Content-Type: {content_type or 'unknown'}")
