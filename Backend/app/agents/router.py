from __future__ import annotations

import re
from dataclasses import dataclass

from Backend.app.core.config import Settings
from Backend.app.schemas.documents import DocumentLanguage, DocumentType


@dataclass(slots=True)
class RoutingDecision:
    doc_type: DocumentType
    language: DocumentLanguage
    reason: str
    confidence: float = 1.0


# ---------------------------------------------------------------------------
# Keyword catalogs — each entry is a plain substring (lowercased).
# Longer / more specific phrases score higher.
# ---------------------------------------------------------------------------

_INVOICE_KEYWORDS: list[tuple[str, float]] = [
    # high-confidence
    ("tax invoice", 1.0),
    ("billing statement", 1.0),
    ("billing invoice", 1.0),
    ("statement of account", 1.0),
    ("account statement", 1.0),
    ("invoice number", 1.0),
    ("invoice no", 1.0),
    ("invoice #", 1.0),
    ("statement number", 0.95),
    ("statement no", 0.95),
    ("statement #", 0.95),
    ("balance due", 0.9),
    ("balance forward", 0.9),
    ("amount due", 0.9),
    ("payment due", 0.85),
    ("bill to", 0.85),
    ("billed to", 0.85),
    ("remit to", 0.8),
    ("invoice", 0.8),
    ("billing", 0.75),
    ("statement", 0.7),
    ("receipt", 0.65),
    # Thai
    ("ใบแจ้งหนี้", 1.0),
    ("ใบกำกับภาษี", 1.0),
    ("ใบเสร็จ", 0.8),
]

_PO_KEYWORDS: list[tuple[str, float]] = [
    ("purchase order", 1.0),
    ("purchase no", 1.0),
    ("purchase number", 1.0),
    ("po number", 1.0),
    ("po no", 1.0),
    ("po #", 1.0),
    ("order confirmation", 0.9),
    ("order number", 0.85),
    ("order no", 0.85),
    ("vendor code", 0.8),
    ("ship to", 0.75),
    ("requisition", 0.75),
    ("purchase", 0.6),
    # Thai
    ("ใบสั่งซื้อ", 1.0),
    ("ใบ po", 1.0),
]

_DELIVERY_KEYWORDS: list[tuple[str, float]] = [
    ("delivery note", 1.0),
    ("delivery order", 1.0),
    ("delivery receipt", 1.0),
    ("dn number", 1.0),
    ("dn no", 1.0),
    ("dn #", 1.0),
    ("delivered by", 0.95),
    ("delivery date", 0.9),
    ("consignment note", 0.9),
    ("packing list", 0.85),
    ("waybill", 0.85),
    ("dispatch note", 0.85),
    ("goods received", 0.8),
    ("received by", 0.75),
    ("delivery", 0.6),
    # Thai
    ("ใบส่งของ", 1.0),
    ("ใบรับสินค้า", 1.0),
]

# Filename stem patterns (regex, case-insensitive)
_INVOICE_FILENAME_RE = re.compile(
    r"(invoice|inv[-_]?\d|billing|statement|receipt|tax[-_]?inv)", re.IGNORECASE
)
_PO_FILENAME_RE = re.compile(
    r"(purchase[-_]?order|[-_]?po[-_]?\d|po[-_]?no|requisition)", re.IGNORECASE
)
_DELIVERY_FILENAME_RE = re.compile(
    r"(delivery[-_]?note|delivery[-_]?order|[-_]?dn[-_]?\d|packing[-_]?list|waybill|dispatch)", re.IGNORECASE
)

# Thai language signals
_THAI_RE = re.compile(r"[\u0E00-\u0E7F]")


class RouterAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def classify(
        self,
        filename: str,
        content_type: str | None = None,
        text_hint: str | None = None,
    ) -> RoutingDecision:
        lower_name = filename.lower()
        lower_text = (text_hint or "").lower()

        # Detect language
        language = DocumentLanguage.TH if _THAI_RE.search(text_hint or "") else DocumentLanguage.EN

        # Score each doc type from filename
        fn_scores: dict[DocumentType, float] = {
            DocumentType.INVOICE: 0.9 if _INVOICE_FILENAME_RE.search(lower_name) else 0.0,
            DocumentType.PURCHASE_ORDER: 0.9 if _PO_FILENAME_RE.search(lower_name) else 0.0,
            DocumentType.DELIVERY_NOTE: 0.9 if _DELIVERY_FILENAME_RE.search(lower_name) else 0.0,
        }

        # Score each doc type from text content
        text_scores: dict[DocumentType, float] = {
            DocumentType.INVOICE: self._score(lower_text, _INVOICE_KEYWORDS),
            DocumentType.PURCHASE_ORDER: self._score(lower_text, _PO_KEYWORDS),
            DocumentType.DELIVERY_NOTE: self._score(lower_text, _DELIVERY_KEYWORDS),
        }

        # Combined score: filename match is a strong signal; text score breaks ties
        combined: dict[DocumentType, float] = {
            dt: fn_scores[dt] + text_scores[dt] for dt in DocumentType
        }

        best_type = max(combined, key=lambda dt: combined[dt])
        best_score = combined[best_type]

        if best_score == 0.0:
            supported = ", ".join(self.settings.supported_doc_type_list)
            raise ValueError(
                f"Cannot classify document '{filename}'. "
                f"No matching keywords found for any supported type ({supported}). "
                f"Content-Type: {content_type or 'unknown'}"
            )

        reason_parts: list[str] = []
        if fn_scores[best_type] > 0:
            reason_parts.append("filename pattern")
        if text_scores[best_type] > 0:
            reason_parts.append(f"text keywords (score={text_scores[best_type]:.2f})")
        reason = f"Classified as {best_type.value} via {' + '.join(reason_parts) or 'heuristic'}"

        return RoutingDecision(
            doc_type=best_type,
            language=language,
            reason=reason,
            confidence=min(1.0, best_score),
        )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _score(text: str, catalog: list[tuple[str, float]]) -> float:
        """Return the maximum keyword weight found in *text*."""
        best = 0.0
        for keyword, weight in catalog:
            if keyword in text:
                best = max(best, weight)
        return best

    def _contains_any(self, source: str, tokens: list[str]) -> bool:  # kept for compat
        return any(token in source for token in tokens)
