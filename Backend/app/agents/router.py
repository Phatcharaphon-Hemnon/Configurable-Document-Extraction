from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.core.config import Settings
from app.schemas.documents import DocumentLanguage, FieldDefinition
from app.schemas.llm_schemas import RoutingResponseSchema
from app.services.sut_genai_client import SutGenAICallError as GeminiCallError, SutGenAIClient as GeminiClient

if TYPE_CHECKING:
    from app.services.knowledge_base import KnowledgeBaseRepository


def _normalize_name(name: str) -> str:
    """Normalize a field name for catalog-matching purposes.

    Rules (order matters):
    1. Strip leading/trailing whitespace.
    2. Lowercase.
    3. Replace any run of whitespace or hyphens with a single underscore.
    4. Collapse multiple consecutive underscores to one.

    This intentionally does NOT perform synonym mapping — "Merchant Name"
    and "vendor_name" will NOT match.  Only case/spacing differences are
    bridged.

    Examples::

        >>> _normalize_name("Total Amount")
        'total_amount'
        >>> _normalize_name("  Invoice-Number  ")
        'invoice_number'
        >>> _normalize_name("total_amount")
        'total_amount'
    """
    s = name.strip().lower()
    s = re.sub(r"[\s\-]+", "_", s)
    s = re.sub(r"_+", "_", s)
    return s


@dataclass(slots=True)
class RoutingDecision:
    doc_type: str
    language: DocumentLanguage
    reason: str
    confidence: float = 1.0
    suggested_fields: list[FieldDefinition] = field(default_factory=list)
    out_of_catalog: bool = False


# Alias mapping for strict mode: common LLM-returned doc_type variants that
# should map to a catalog doc_type.  Keys are lowercase.
_STRICT_ALIASES: dict[str, str] = {
    "purchase_order": "po",
    "purchase order": "po",
    "p.o.": "po",
    "invoices": "invoice",
    "delivery note": "delivery_note",
    "dn": "delivery_note",
}


class RouterAgent:
    """Document-classification router.

    Supports two modes controlled by ``schema_mode``:

    * **open** (default) — proposes whatever document type it recognizes and a
      list of fields per document, with no restriction on doc_type values.
    * **strict** — constrains doc_type to only the types found in the
      knowledge-base field catalog (e.g. invoice, po, delivery_note).  If the
      model returns an out-of-catalog type, an alias lookup is attempted before
      falling back to ``out_of_catalog=True``.
    """

    def __init__(
        self,
        settings: Settings,
        knowledge_base: "KnowledgeBaseRepository | None" = None,
        schema_mode: str = "open",
    ) -> None:
        self.settings = settings
        self.schema_mode = schema_mode
        self._client = GeminiClient(settings)
        self._knowledge_base = knowledge_base

        # Derive allowed doc types from catalog when in strict mode.
        self._allowed_doc_types: list[str] = []
        if self.schema_mode == "strict":
            if self._knowledge_base is None:
                raise ValueError(
                    "SCHEMA_MODE=strict requires a knowledge base with a "
                    "field_catalog directory"
                )
            self._allowed_doc_types = self._knowledge_base.list_catalog_doc_types()
            if not self._allowed_doc_types:
                raise ValueError(
                    "SCHEMA_MODE=strict requires at least one field catalog "
                    "JSON file in the knowledge base field_catalog directory"
                )

    @staticmethod
    def _reconcile_with_catalog(
        ai_fields: list[FieldDefinition],
        catalog_fields: list[FieldDefinition],
    ) -> list[FieldDefinition]:
        """Merge AI-proposed fields with the on-disk field catalog.

        Algorithm
        ---------
        a. Build a ``{normalized_name: FieldDefinition}`` lookup from
           *catalog_fields* using :func:`_normalize_name`.
        b. For each field in *ai_fields*: if its normalized name matches a
           catalog entry, replace **only** ``name`` and ``likely_required``
           with the catalog's canonical values — the AI's ``description`` is
           kept because it is often more specific/contextual.  Track which
           catalog names were matched.
        c. Append any catalog field that was NOT matched by an AI-proposed
           field, using the catalog's ``name``, ``required`` as
           ``likely_required``, and ``validation_rule`` (if present) as the
           ``description`` — so the Extractor still looks for it even if the
           Router's own guess missed it.
        d. AI-proposed fields that don't match any catalog entry are left
           as-is (open-schema behaviour — novel fields are preserved).
        e. The input lists are never mutated.
        """
        # Build normalized lookup from catalog
        catalog_lookup: dict[str, FieldDefinition] = {
            _normalize_name(cf.name): cf for cf in catalog_fields
        }

        reconciled: list[FieldDefinition] = []
        matched_catalog_names: set[str] = set()

        for ai_field in ai_fields:
            norm = _normalize_name(ai_field.name)
            if norm in catalog_lookup:
                catalog_entry = catalog_lookup[norm]
                matched_catalog_names.add(norm)
                # Override name + likely_required from catalog; keep AI description
                reconciled.append(
                    FieldDefinition(
                        name=catalog_entry.name,
                        description=ai_field.description,
                        likely_required=catalog_entry.likely_required,
                        type=ai_field.type,
                        validation_rule=ai_field.validation_rule,
                    )
                )
            else:
                # No catalog match — keep the AI field unchanged (open-schema)
                reconciled.append(ai_field.model_copy())

        # Append catalog fields the AI never proposed
        for norm_key, catalog_entry in catalog_lookup.items():
            if norm_key not in matched_catalog_names:
                reconciled.append(
                    FieldDefinition(
                        name=catalog_entry.name,
                        description=catalog_entry.validation_rule,
                        likely_required=catalog_entry.likely_required,
                        type=catalog_entry.type,
                        validation_rule=catalog_entry.validation_rule,
                    )
                )

        return reconciled

    def _build_open_prompt(self, filename: str, text_hint: str | None) -> str:
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
        return "\n\n".join(prompt_parts)

    def _build_strict_prompt(self, filename: str, text_hint: str | None) -> str:
        allowed_str = ", ".join(self._allowed_doc_types)
        prompt_parts = [
            "You are a document classification router.",
            f"Look at this document and classify it as exactly one of: {allowed_str}.",
            "Also detect the primary language: en (English), th (Thai), or other.",
            "Propose a list of fields you would expect to find on a document of this type, based "
            "on what you can actually see. For each suggested field, give a short name, a brief "
            "description, and mark likely_required=true only if the document would be essentially "
            "useless/unidentifiable without that field (e.g. an invoice number, a total amount). "
            "Most fields should be likely_required=false.",
            "IMPORTANT — Field naming convention: When you see a value on the document that "
            "is a more specific variant of a common concept, use the short generic field name "
            "and put the specific nuance in the description. For example:\n"
            '  - "Grand Total (incl. GST)" on the document → name: "total_amount", '
            'description: "Grand total including GST"\n'
            '  - "Ship-to Address" on the document → name: "delivery_address", '
            'description: "Shipping destination address"\n'
            "Do NOT invent qualified names like \"total_amount_incl_gst\" or "
            "\"ship_to_address\" — keep names generic so they match the field catalog.",
            "Base your decision on the actual document content — not the filename.",
            "Return your confidence (0.0–1.0) reflecting how certain you are.",
            f"Filename (metadata only, do not rely on it): {filename}",
        ]
        if text_hint and text_hint.strip():
            prompt_parts.append(f"Document text hint:\n{text_hint.strip()}")
        return "\n\n".join(prompt_parts)

    async def classify(
        self,
        filename: str,
        text_hint: str | None = None,
    ) -> RoutingDecision:
        if not (text_hint and text_hint.strip()):
            raise GeminiCallError(
                "Router requires document text to classify the document"
            )

        if self.schema_mode == "strict":
            prompt = self._build_strict_prompt(filename, text_hint)
        else:
            prompt = self._build_open_prompt(filename, text_hint)

        try:
            result = await self._client.generate_structured(
                model=self.settings.router_model_name,
                prompt=prompt,
                response_schema=RoutingResponseSchema,
            )
        except GeminiCallError:
            raise
        except Exception as exc:
            raise GeminiCallError(f"Router classification failed: {exc}") from exc

        parsed = result.parsed
        assert isinstance(parsed, RoutingResponseSchema)

        doc_type = parsed.doc_type
        out_of_catalog = False

        if self.schema_mode == "strict":
            norm_dt = doc_type.strip().lower()
            allowed_normalized = {dt.strip().lower(): dt for dt in self._allowed_doc_types}
            
            if norm_dt in allowed_normalized:
                doc_type = allowed_normalized[norm_dt]
            elif norm_dt in _STRICT_ALIASES:
                alias = _STRICT_ALIASES[norm_dt]
                if alias in allowed_normalized:
                    doc_type = allowed_normalized[alias]
                else:
                    out_of_catalog = True
            else:
                out_of_catalog = True

        suggested = [
            FieldDefinition(
                name=f.name,
                description=f.description,
                likely_required=f.likely_required,
            )
            for f in parsed.suggested_fields
        ]

        # Reconcile with the on-disk field catalog when a knowledge base is
        # available.  For doc types with no matching catalog file this is a
        # no-op (get_catalog_fields returns []), preserving open-schema
        # behaviour for novel document types.
        if self._knowledge_base is not None:
            catalog_fields = self._knowledge_base.get_catalog_fields(doc_type)
            if catalog_fields:
                suggested = self._reconcile_with_catalog(suggested, catalog_fields)

        return RoutingDecision(
            doc_type=doc_type,
            language=parsed.language,
            reason=parsed.reason,
            confidence=parsed.confidence,
            suggested_fields=suggested,
            out_of_catalog=out_of_catalog,
        )