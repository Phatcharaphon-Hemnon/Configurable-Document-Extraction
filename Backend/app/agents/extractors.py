from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any


from app.agents.router import _normalize_name
from app.core.config import Settings
from app.schemas.documents import ExtractionField, FieldDefinition
from app.schemas.llm_schemas import ExtractionResponseSchema
from app.services.field_aliases import resolve_field_alias
from app.services.sut_genai_client import SutGenAICallError as GeminiCallError, SutGenAIClient as GeminiClient

logger = logging.getLogger(__name__)


@dataclass
class ExtractionContext:
    text: str
    doc_type: str
    suggested_fields: list[FieldDefinition] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    few_shot_examples: list[dict] | None = None
    image_bytes: bytes | None = None
    image_media_type: str | None = None


def _parse_extraction_response(
    parsed: ExtractionResponseSchema,
    suggested_names: set[str],
    doc_type: str = "",
) -> tuple[dict[str, ExtractionField], dict[str, ExtractionField]]:
    """Split extracted fields into (matches a suggested field, extra/unlisted field)."""
    extracted: dict[str, ExtractionField] = {}
    additional: dict[str, ExtractionField] = {}

    suggested_lookup: dict[str, str] = {}
    for name in suggested_names:
        norm = _normalize_name(name)
        resolved = resolve_field_alias(doc_type, norm)
        suggested_lookup[norm] = name
        suggested_lookup[resolved] = name

    for entry in parsed.fields:
        if entry.value is None:
            logger.debug("Extractor dropped field '%s' (null value) for doc_type=%s", entry.name, doc_type)
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
            norm_entry = _normalize_name(entry.name)
            resolved_entry = resolve_field_alias(doc_type, norm_entry)
            if resolved_entry in suggested_lookup:
                canonical = suggested_lookup[resolved_entry]
                extracted[canonical] = item
            elif norm_entry in suggested_lookup:
                canonical = suggested_lookup[norm_entry]
                extracted[canonical] = item
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
        has_image = bool(context.image_bytes)

        if not has_text and not has_image:
            raise GeminiCallError("Extractor requires document text or an image")

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
            "- Return a JSON object with a 'fields' array. Each entry MUST have: name, value, "
            "confidence (0.0-1.0), source_span (exact text from the document), likely_required.\n"
            "- Extract every suggested field you can find. Also include any OTHER clearly labeled "
            "data not in the list (e.g. discount, PO reference, payment terms) — never discard information.\n"
            "- Omit a field entirely if truly absent — do NOT hallucinate values.\n"
            "- Numeric amounts: return numbers without currency symbols.\n"
            "- Dates: preserve the document's original format.\n"
            "- Itemized lists (line items, products): return an array of objects (one per row) with "
            "whatever of description/quantity/unit_price/amount is present, even for a single row.\n"
            "\n"
            "Extraction procedure:\n"
            "1. Check EVERY suggested field against the ENTIRE document — including footers, "
            "small-print tables, and summary boxes — before deciding a field is absent.\n"
            "2. Pay special attention to commonly missed fields:\n"
            "   - Tax/VAT/GST breakdown tables: extract subtotal and tax amount separately from "
            "the grand total.\n"
            "   - Currency: infer from symbols/codes (RM, MYR, $, USD, ฿, THB, €, EUR) or strong "
            "context (e.g. Malaysian registration → MYR). Omit if no clue.\n"
            "   - Tax ID / GST ID / VAT number: often near the seller's name/address.\n"
            "   - Payment mechanics: amount paid/tendered and change given, if shown.\n"
            "3. Re-check: for every suggested field NOT extracted, confirm it is truly absent.\n"
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

        if has_image and context.image_bytes and context.image_media_type:
            result = await self._client.generate_structured_with_image(
                model=self.settings.recommended_extraction_model_name,
                prompt=prompt,
                image_bytes=context.image_bytes,
                image_media_type=context.image_media_type,
                response_schema=ExtractionResponseSchema,
            )
        else:
            result = await self._client.generate_structured(
                model=self.settings.recommended_extraction_model_name,
                prompt=prompt,
                response_schema=ExtractionResponseSchema,
            )
        logger.info(
            "Extractor tokens: doc_type=%s prompt=%s completion=%s total=%s",
            context.doc_type,
            result.prompt_tokens,
            result.completion_tokens,
            result.total_tokens,
        )

        parsed = result.parsed

        logger.info(
            "Extractor raw response: doc_type=%s fields_returned=%d raw_text_preview=%s",
            context.doc_type,
            len(parsed.fields) if parsed and parsed.fields else 0,
            (result.raw_text or "")[:300],
        )

        if not parsed or not parsed.fields:
            has_image = bool(context.image_bytes)
            if has_image:
                logger.warning(
                    "Extractor got zero fields for %s (doc_type=%s, has_image=%s) — "
                    "check image quality/orientation or GitHub Models vision response",
                    context.metadata.get("filename"),
                    context.doc_type,
                    has_image,
                )

        assert isinstance(parsed, ExtractionResponseSchema)
        extracted, additional = _parse_extraction_response(parsed, suggested_names, doc_type=context.doc_type)
        return extracted, additional