from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings
from app.schemas.documents import ExtractionField, FieldDefinition
from app.schemas.llm_schemas import ExtractionResponseSchema
from app.services.sut_genai_client import SutGenAICallError as GeminiCallError, SutGenAIClient as GeminiClient


@dataclass
class ExtractionContext:
    text: str
    doc_type: str
    suggested_fields: list[FieldDefinition] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    few_shot_examples: list[dict] | None = None


def _parse_extraction_response(
    parsed: ExtractionResponseSchema,
    suggested_names: set[str],
) -> tuple[dict[str, ExtractionField], dict[str, ExtractionField]]:
    """Split extracted fields into (matches a suggested field, extra/unlisted field)."""
    extracted: dict[str, ExtractionField] = {}
    additional: dict[str, ExtractionField] = {}
    for entry in parsed.fields:
        if entry.value is None:
            continue
        item = ExtractionField(
            value=entry.value,
            confidence=entry.confidence,
            source_span=entry.source_span,
            likely_required=entry.likely_required,
        )
        if entry.name in suggested_names:
            extracted[entry.name] = item
        else:
            additional[entry.name] = item
    return extracted, additional


class OpenSchemaExtractor:
    """Single generic extractor for ANY document type. Instead of a hardcoded
    per-type field catalog, it extracts based on the suggested_fields the
    Router proposed for this specific document, and additionally reports any
    other clearly-labeled fields it notices that weren't in that list
    (captured separately as 'additional' fields, never discarded).
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = GeminiClient(settings)

    async def extract(self, context: ExtractionContext) -> tuple[dict[str, ExtractionField], dict[str, ExtractionField]]:
        has_text = bool(context.text.strip())

        if not has_text:
            raise GeminiCallError("Extractor requires document text")

        suggested_names = {f.name for f in context.suggested_fields}
        fields_desc = {
            f.name: (f.description or "") + (" [likely required]" if f.likely_required else "")
            for f in context.suggested_fields
        }
        fields_json = json.dumps(fields_desc, indent=2) if fields_desc else "(none proposed — extract whatever you see)"

        # Build the optional few-shot block (only when examples are present)
        few_shot_block = ""
        if context.few_shot_examples:
            compact = json.dumps(context.few_shot_examples, ensure_ascii=False, separators=(",", ":"))
            few_shot_block = (
                "Reference examples of correctly extracted fields for this document type "
                "(for pattern guidance only — do not copy values):\n"
                f"{compact}\n\n"
            )

        base_rules = (
            "Rules:\n"
            "- Return a JSON object with a 'fields' array.\n"
            "- Each array entry must have: name, value, confidence (0.0-1.0), source_span "
            "(exact text visible/present in the document), and likely_required (true only for "
            "fields essential to identify/use this document).\n"
            "- Extract every field in the suggested list that you can actually find.\n"
            "- If you notice OTHER clearly labeled data on the document that is not in the "
            "suggested list (e.g. a discount line, a PO reference, payment terms), INCLUDE it "
            "too as an extra entry — do not discard information just because it wasn't suggested.\n"
            "- For numeric amounts, return the number without currency symbols.\n"
            "- For dates, preserve the original format shown in the document.\n"
            "- For itemized lists (line items, products, etc.), return an array of objects — "
            "one object per row — even if there is only a single row. Include whatever of "
            "description/quantity/unit_price/amount is present in that row.\n"
            "- Omit a suggested field entirely if it is truly not present — do NOT invent or "
            "hallucinate a value to fill it in.\n"
            "\n"
            "Extraction procedure:\n"
            "1. Go through the suggested field list ONE BY ONE and actively check the document "
            "for each — do not stop after finding the obvious ones (name, date, total). Read "
            "the WHOLE document, including footers, small-print tables, and summary boxes, "
            "before deciding a field is absent.\n"
            "2. Pay special attention to these commonly-missed field types:\n"
            "   - Tax/VAT/GST breakdown tables (e.g. 'GST Summary', 'Tax Summary'): these "
            "usually contain the subtotal (amount before tax) and the tax amount separately "
            "from the grand total. Extract both, even if the grand total is already captured "
            "elsewhere.\n"
            "   - Currency: infer from explicit symbols/codes (RM, MYR, $, USD, ฿, THB, €, "
            "EUR, etc.) or from strong contextual clues (e.g. a Malaysian company registration "
            "number or 'GST ID' implies MYR). If there is genuinely no clue, omit the field "
            "rather than guessing.\n"
            "   - Tax ID / GST ID / VAT number: often printed near the seller's name or "
            "address, sometimes labeled separately from the company registration number.\n"
            "   - Payment mechanics: amount paid/tendered and change given, if shown.\n"
            "3. Before finalizing your answer, re-check the document once more against the "
            "suggested field list: for every suggested field you have NOT extracted, confirm it "
            "is truly absent rather than just easy to miss.\n"
        )

        prompt = (
            "You are a document data extraction expert operating with an OPEN schema — "
            "this document type may not match any fixed catalog.\n\n"
            f"Document type (as classified by the router): {context.doc_type}\n\n"
            f"Suggested fields to look for:\n{fields_json}\n\n"
            f"{few_shot_block}"
            f"{base_rules}"
            f"\nDocument text:\n{context.text}\n"
        )

        result = await self._client.generate_structured(
            model=self.settings.recommended_extraction_model_name,
            prompt=prompt,
            response_schema=ExtractionResponseSchema,
        )

        parsed = result.parsed
        assert isinstance(parsed, ExtractionResponseSchema)
        extracted, additional = _parse_extraction_response(parsed, suggested_names)
        if not extracted and not additional:
            raise GeminiCallError(
                "Extractor returned no fields",
                request_summary=result.request_summary,
                raw_response=result.raw_response,
            )
        return extracted, additional