from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from Backend.app.core.config import Settings
from Backend.app.schemas.documents import DocumentType, ExtractionField
from Backend.app.schemas.gemini_schemas import ExtractionResponseSchema
from Backend.app.services.gemini_client import GeminiCallError, GeminiClient

_KB_ROOT = Path(__file__).resolve().parents[1] / "data" / "knowledge_base" / "few_shot"

_FEW_SHOT_DIR_MAP: dict[DocumentType, str] = {
    DocumentType.INVOICE: "invoice",
    DocumentType.PURCHASE_ORDER: "po",
    DocumentType.DELIVERY_NOTE: "delivery_note",
}

_INVOICE_FIELDS = {
    "invoice_number": "The invoice or billing statement number/ID",
    "statement_number": "Statement number if present",
    "customer_id": "Customer ID or account number",
    "bill_to_name": "Name of the company or person being billed",
    "bill_to_address": "Full billing address",
    "vendor_name": "Name of the vendor or issuer",
    "vendor_address": "Full vendor address",
    "vendor_phone": "Vendor phone number",
    "vendor_email": "Vendor email address",
    "statement_date": "Date the statement was issued",
    "payment_due_date": "Payment due date",
    "balance_due": "Total balance due amount (numeric)",
    "current_balance": "Current balance amount (numeric)",
    "currency": "Currency code or symbol (e.g. PHP, USD, THB)",
    "line_items": "List of line items with date, type, description, payment, amount, balance",
}

_PO_FIELDS = {
    "po_number": "Purchase order number",
    "order_date": "Date the order was placed",
    "supplier_name": "Supplier or vendor name",
    "supplier_address": "Supplier address",
    "buyer_name": "Buyer or company name",
    "buyer_address": "Buyer address",
    "delivery_date": "Expected delivery date",
    "total_amount": "Total order amount (numeric)",
    "currency": "Currency code or symbol",
    "line_items": "List of ordered items with description, quantity, unit price, total",
    "payment_terms": "Payment terms",
    "notes": "Any additional notes or remarks",
}

_DELIVERY_NOTE_FIELDS = {
    "delivery_note_number": "Delivery note or DN number",
    "delivery_date": "Date of delivery",
    "delivered_by": "Courier or delivery person/company name",
    "recipient_name": "Name of the recipient",
    "recipient_address": "Delivery address",
    "sender_name": "Sender or shipper name",
    "sender_address": "Sender address",
    "items": "List of delivered items with description and quantity",
    "total_weight": "Total weight if present",
    "notes": "Any additional notes or remarks",
}

_DOC_FIELD_MAP: dict[DocumentType, dict[str, str]] = {
    DocumentType.INVOICE: _INVOICE_FIELDS,
    DocumentType.PURCHASE_ORDER: _PO_FIELDS,
    DocumentType.DELIVERY_NOTE: _DELIVERY_NOTE_FIELDS,
}


def _load_few_shot_examples(doc_type: DocumentType, max_examples: int = 5) -> list[dict[str, Any]]:
    subdir = _FEW_SHOT_DIR_MAP.get(doc_type, "")
    examples_dir = _KB_ROOT / subdir
    if not examples_dir.exists():
        return []
    examples: list[dict[str, Any]] = []
    for path in sorted(examples_dir.glob("*.json"))[:max_examples]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                examples.append(json.load(f))
        except Exception:
            continue
    return examples


def _build_few_shot_block(examples: list[dict[str, Any]]) -> str:
    if not examples:
        return ""
    lines: list[str] = ["Few-shot examples (input → expected output):"]
    for i, ex in enumerate(examples, 1):
        desc = ex.get("description", f"Example {i}")
        input_text = ex.get("input_text", "")
        output = ex.get("output", {})
        lines.append(f"\n--- Example {i}: {desc} ---")
        lines.append(f"Input:\n{input_text}")
        lines.append(f"Expected output:\n{json.dumps(output, ensure_ascii=False, indent=2)}")
    return "\n".join(lines)


@dataclass
class ExtractionContext:
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    image_bytes: bytes | None = None
    image_mime_type: str | None = None


def _parse_extraction_response(parsed: ExtractionResponseSchema) -> dict[str, ExtractionField]:
    extracted: dict[str, ExtractionField] = {}
    for entry in parsed.fields:
        if entry.value is None:
            continue
        extracted[entry.name] = ExtractionField(
            value=entry.value,
            confidence=entry.confidence,
            source_span=entry.source_span,
        )
    return extracted


class BaseExtractor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = GeminiClient(settings)

    def extract(self, context: ExtractionContext) -> dict[str, ExtractionField]:
        raise NotImplementedError

    def _gemini_extract(self, doc_type: DocumentType, context: ExtractionContext) -> dict[str, ExtractionField]:
        has_image = context.image_bytes is not None and context.image_mime_type is not None
        has_text = bool(context.text.strip())

        if not has_image and not has_text:
            raise GeminiCallError("Extractor requires either document text or an image")

        fields_desc = _DOC_FIELD_MAP.get(doc_type, {})
        fields_json = json.dumps(fields_desc, indent=2)
        few_shot_block = _build_few_shot_block(_load_few_shot_examples(doc_type))

        if has_image:
            prompt = (
                "You are a document data extraction expert. "
                "Look at the document image and extract ALL visible fields.\n\n"
                f"Document type: {doc_type.value}\n\n"
                f"Fields to extract:\n{fields_json}\n\n"
                f"{few_shot_block}\n\n"
                "Rules:\n"
                "- Return a JSON object with a 'fields' array.\n"
                "- Each array entry must have: name, value, confidence (0.0-1.0), source_span (exact text visible in the image).\n"
                "- For numeric amounts, return the number without currency symbols.\n"
                "- For dates, preserve the original format shown in the image.\n"
                "- For line_items or items, return an array of objects as the value.\n"
                "- Omit fields that are not visible in the image.\n"
                "- Do NOT invent or hallucinate values. Only extract what is explicitly visible.\n"
                "- If text is present in the context, use it to help but the image is the ground truth.\n"
            )
            if has_text:
                prompt += f"\nDocument text (OCR/Hint):\n{context.text}\n"
            image_bytes = context.image_bytes
            image_mime_type = context.image_mime_type
        else:
            prompt = (
                "You are a document data extraction expert. "
                "Extract ALL available fields from the document text below.\n\n"
                f"Document type: {doc_type.value}\n\n"
                f"Fields to extract (extract every field you can find, skip only if truly absent):\n{fields_json}\n\n"
                f"{few_shot_block}\n\n"
                "Rules:\n"
                "- Return a JSON object with a 'fields' array.\n"
                "- Each array entry must have: name, value, confidence (0.0-1.0), source_span (exact text from document).\n"
                "- For numeric amounts, return the number without currency symbols.\n"
                "- For dates, preserve the original format from the document.\n"
                "- For line_items or items, return an array of objects as the value.\n"
                "- Omit fields that are not present in the document.\n"
                "- Do NOT invent or hallucinate values. Only extract what is explicitly in the text.\n\n"
                f"Document text:\n{context.text}\n"
            )
            image_bytes = None
            image_mime_type = None

        result = self._client.generate_structured(
            model=self.settings.recommended_extraction_model_name,
            prompt=prompt,
            response_schema=ExtractionResponseSchema,
            image_bytes=image_bytes,
            image_mime_type=image_mime_type,
        )

        parsed = result.parsed
        assert isinstance(parsed, ExtractionResponseSchema)
        extracted = _parse_extraction_response(parsed)
        if not extracted:
            raise GeminiCallError(
                "Extractor returned no fields",
                request_summary=result.request_summary,
                raw_response=result.raw_response,
            )
        return extracted


class InvoiceExtractor(BaseExtractor):
    def extract(self, context: ExtractionContext) -> dict[str, ExtractionField]:
        result = self._gemini_extract(DocumentType.INVOICE, context)
        if "total_amount" in result and "balance_due" not in result:
            result["balance_due"] = result.pop("total_amount")
        return result


class PurchaseOrderExtractor(BaseExtractor):
    def extract(self, context: ExtractionContext) -> dict[str, ExtractionField]:
        return self._gemini_extract(DocumentType.PURCHASE_ORDER, context)


class DeliveryNoteExtractor(BaseExtractor):
    def extract(self, context: ExtractionContext) -> dict[str, ExtractionField]:
        return self._gemini_extract(DocumentType.DELIVERY_NOTE, context)


class ExtractorFactory:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def create(self, doc_type: DocumentType) -> BaseExtractor:
        if doc_type == DocumentType.INVOICE:
            return InvoiceExtractor(self.settings)
        if doc_type == DocumentType.PURCHASE_ORDER:
            return PurchaseOrderExtractor(self.settings)
        if doc_type == DocumentType.DELIVERY_NOTE:
            return DeliveryNoteExtractor(self.settings)
        raise ValueError(f"Unsupported extractor for doc type: {doc_type}")
