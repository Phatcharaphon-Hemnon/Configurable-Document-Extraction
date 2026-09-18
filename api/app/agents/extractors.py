"""Document extractors — one agent per fixed document type.

Design (project spec):
- 3 extractors: InvoiceExtractor, PurchaseOrderExtractor, DeliveryNoteExtractor.
- Each prompt embeds the COMPACT field catalog (name + type + required +
  short Thai description) to minimize tokens. Few-shot examples are optional
  (default OFF).
- Field names returned by the LLM are matched EXACTLY against the catalog
  (normalization only — never aliases/synonyms, never Thai display labels).
  Unknown labeled fields are kept, flagged is_new_field, and registered into
  the catalog by the service.
- Document text is sanitized before entering any prompt.
"""

from __future__ import annotations

import json
import logging
from datetime import date

from app.core.config import Settings
from app.core.security import sanitize_document_text
from app.prompts.registry import get_prompt
from app.schemas.documents import DOC_TYPES, DocType, ExtractedField
from app.schemas.llm_schemas import ExtractionResponseSchema
from app.services.client import Client, ClientError
from app.services.field_catalog import FieldCatalog, is_placeholder_value, normalize_field_name

logger = logging.getLogger(__name__)

_EXTRACTOR_SPEC = get_prompt("extractor")
_COMMON_RULES = _EXTRACTOR_SPEC.parts["rules"]


def _build_prompt(doc_label: str, compact_catalog: str, text: str, few_shot: list[dict] | None) -> str:
    parts = [
        _EXTRACTOR_SPEC.render("header", doc_label=doc_label),
        _EXTRACTOR_SPEC.render("catalog_intro", compact_catalog=compact_catalog),
    ]
    if few_shot:
        parts.append(
            _EXTRACTOR_SPEC.render(
                "examples_intro",
                examples_json=sanitize_document_text(
                    json.dumps(few_shot, ensure_ascii=False, separators=(",", ":"))
                ),
            )
        )
    parts.append(_COMMON_RULES)
    if text.strip():
        parts.append(_EXTRACTOR_SPEC.render("text_intro", text=text.strip()))
    elif not text.strip():
        parts.append(_EXTRACTOR_SPEC.parts["image_note"])
    return "\n\n".join(parts)


def _coerce_value(raw: object) -> str | float | date | None:
    """Map the LLM's JSON value onto the ExtractedField value union."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return str(raw)
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, (list, dict)):
        return json.dumps(raw, ensure_ascii=False)
    text = str(raw).strip()
    return text or None  # further placeholder filtering happens via is_placeholder_value


class BaseExtractor:
    doc_type: DocType
    doc_label: str

    def __init__(self, settings: Settings, catalog: FieldCatalog, client: Client | None = None) -> None:
        self.settings = settings
        self.catalog = catalog
        self._client = client or Client(settings)

    async def extract_call(
        self,
        text: str,
        image_bytes: bytes | None = None,
        image_media_type: str | None = None,
        few_shot: list[dict] | None = None,
        page_number: int = 1,
    ):
        """Typed extraction call for ONE page (no shared mutable state).

        Returns ExtractionCallResult bound to (doc_type, page_number).
        Test doubles that stub `extract` are honored: when the instance
        carries its own `extract` attribute (e.g. AsyncMock in unit tests),
        this delegates to it and wraps the tuple with empty tables.
        """
        from unittest.mock import AsyncMock, MagicMock

        from app.schemas.documents import ExtractionCallResult as _ECR

        inst_extract = self.__dict__.get("extract")
        if isinstance(inst_extract, (AsyncMock, MagicMock)):
            wrapped = await inst_extract(text)
            # Support doubles returning either a tuple or a call result.
            if isinstance(wrapped, tuple):
                fields, new_names = wrapped
                return _ECR(
                    doc_type=self.doc_type,
                    page_number=page_number,
                    fields=list(fields),
                    tables=[],
                    new_field_names=list(new_names or []),
                )
            if isinstance(wrapped, _ECR):
                wrapped.doc_type = self.doc_type
                wrapped.page_number = page_number
                return wrapped
            raise ClientError(f"{self.doc_label} extractor double returned {type(wrapped).__name__}")
        return await self._run_call(
            text=text,
            image_bytes=image_bytes,
            image_media_type=image_media_type,
            few_shot=few_shot,
            page_number=page_number,
        )

    async def _run_call(
        self,
        text: str,
        image_bytes: bytes | None = None,
        image_media_type: str | None = None,
        few_shot: list[dict] | None = None,
        page_number: int = 1,
    ):
        """Real LLM extraction call (page-isolated, no instance state)."""
        import copy

        from app.schemas.documents import ExtractionCallResult
        has_text = bool(text and text.strip())
        if not has_text and not image_bytes:
            raise ClientError(f"{self.doc_label} extractor requires text or an image")

        known = self.catalog.known_names(self.doc_type)
        prompt = _build_prompt(
            self.doc_label,
            self.catalog.compact_for_prompt(self.doc_type),
            sanitize_document_text(text),
            few_shot,
        )

        if image_bytes and image_media_type:
            result = await self._client.generate_structured_with_image(
                model=self.settings.vision_model_name,
                prompt=prompt,
                image_bytes=image_bytes,
                image_media_type=image_media_type,
                response_schema=ExtractionResponseSchema,
                max_tokens=self.settings.extraction_max_tokens,
                disable_reasoning=True,
            )
        else:
            result = await self._client.generate_structured(
                model=self.settings.extraction_model_name,
                prompt=prompt,
                response_schema=ExtractionResponseSchema,
                max_tokens=self.settings.extraction_max_tokens,
                disable_reasoning=True,
            )

        logger.info(
            "Extractor[%s]: fields=%d prompt_tokens=%s completion_tokens=%s",
            self.doc_type,
            len(result.parsed.fields) if result.parsed else 0,
            result.prompt_tokens,
            result.completion_tokens,
        )

        # Page-isolated: deep-copy tables so no list/dict is shared across
        # pages or calls. No instance state is retained.
        tables = copy.deepcopy(result.parsed.tables or [])
        fields: list[ExtractedField] = []
        seen: set[str] = set()
        for entry in result.parsed.fields:
            normalized = normalize_field_name(entry.name)
            if not normalized or normalized in seen:
                continue
            value = _coerce_value(entry.value)
            if is_placeholder_value(value):
                # 'N/A', '-', '' … mean the field is ABSENT — omit it entirely.
                continue
            seen.add(normalized)
            try:
                conf = float(entry.confidence)
            except Exception:
                conf = 0.0
            fields.append(
                ExtractedField(
                    name=normalized,
                    value=value,
                    confidence=max(0.0, min(1.0, conf)),
                    source_span=entry.source_span,
                    is_new_field=normalized not in known,
                )
            )
        return ExtractionCallResult(
            doc_type=self.doc_type,
            page_number=page_number,
            fields=fields,
            tables=tables,
            new_field_names=[],
        )

    async def extract(
        self,
        text: str,
        image_bytes: bytes | None = None,
        image_media_type: str | None = None,
        few_shot: list[dict] | None = None,
    ) -> tuple[list[ExtractedField], list[str]]:
        """Backward-compatible wrapper returning (fields, new_field_names).

        Prefer `extract_call` for new code (typed tables + page binding).
        """
        call = await self._run_call(
            text=text,
            image_bytes=image_bytes,
            image_media_type=image_media_type,
            few_shot=few_shot,
            page_number=1,
        )
        return call.fields, call.new_field_names


class InvoiceExtractor(BaseExtractor):
    doc_type = "invoice"
    doc_label = "invoice / tax invoice / POS receipt"


class PurchaseOrderExtractor(BaseExtractor):
    doc_type = "purchase_order"
    doc_label = "purchase order"


class DeliveryNoteExtractor(BaseExtractor):
    doc_type = "delivery_note"
    doc_label = "delivery note / delivery order"


def build_extractors(settings: Settings, catalog: FieldCatalog) -> dict[DocType, BaseExtractor]:
    extractors: dict[DocType, BaseExtractor] = {
        "invoice": InvoiceExtractor(settings, catalog),
        "purchase_order": PurchaseOrderExtractor(settings, catalog),
        "delivery_note": DeliveryNoteExtractor(settings, catalog),
    }
    assert set(extractors) == set(DOC_TYPES)
    return extractors
