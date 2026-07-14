from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from app.schemas.documents import DocumentLanguage, FieldDefinition, TemplateSchema

# Maximum total serialized size (chars) for the few-shot block injected into
# the prompt.  Keeps token cost bounded regardless of how many example files
# exist in a folder.
_FEW_SHOT_MAX_CHARS = 1500


def _cap_by_size(examples: list[dict], max_chars: int) -> list[dict]:
    """Return the longest prefix of *examples* whose total JSON serialization
    fits within *max_chars* characters."""
    result: list[dict] = []
    total = 0
    for ex in examples:
        serialized = json.dumps(ex, ensure_ascii=False, separators=(",", ":"))
        if total + len(serialized) > max_chars and result:
            break
        result.append(ex)
        total += len(serialized)
    return result


class KnowledgeBaseRepository:
    """Open-schema knowledge base. Instead of loading three hardcoded catalog
    files (invoice/po/delivery_note), this scans the field_catalog directory
    for ANY *.json files present and turns each into a TemplateSchema whose
    doc_type is derived from the filename. This is purely informational
    (used by GET /templates) — the actual Router/Extractor pipeline does not
    depend on these files at all; it proposes fields dynamically per document.
    """

    def __init__(self, base_path: Path) -> None:
        self.base_path = base_path
        # In-memory cache: doc_type (lower) -> list of field dicts
        self._few_shot_cache: dict[str, list[dict]] = {}
        # Sentinel to distinguish "not yet loaded" from "loaded but empty"
        self._few_shot_loaded: set[str] = set()

    def list_templates(self) -> list[TemplateSchema]:
        base = self.base_path / "field_catalog"
        templates: list[TemplateSchema] = []

        if base.exists():
            for path in sorted(base.glob("*.json")):
                payload = self._load(path)
                if not payload:
                    continue
                template = self._parse_template(path, payload)
                if template is not None:
                    templates.append(template)

        if templates:
            return templates

        # Minimal fallback if no catalog files are present on disk — kept as
        # illustrative examples only, not a restriction on supported types.
        return [
            TemplateSchema(
                doc_type="invoice",
                description="Example invoice extraction schema (illustrative — the router is not limited to this)",
                language_support=[DocumentLanguage.EN, DocumentLanguage.TH],
                fields=[
                    FieldDefinition(name="invoice_number", type="string", likely_required=True, validation_rule="Non-empty identifier"),
                    FieldDefinition(name="total_amount", type="number", likely_required=True, validation_rule="Must be positive"),
                    FieldDefinition(name="currency", type="string", likely_required=False, validation_rule="ISO currency code"),
                ],
            ),
            TemplateSchema(
                doc_type="purchase_order",
                description="Example purchase order extraction schema (illustrative)",
                language_support=[DocumentLanguage.EN, DocumentLanguage.TH],
                fields=[
                    FieldDefinition(name="po_number", type="string", likely_required=True, validation_rule="Non-empty identifier"),
                    FieldDefinition(name="supplier_name", type="string", likely_required=True, validation_rule="Non-empty value"),
                    FieldDefinition(name="order_date", type="date", likely_required=False, validation_rule="ISO 8601 date"),
                ],
            ),
        ]

    # ------------------------------------------------------------------
    # Catalog field lookup
    # ------------------------------------------------------------------

    def get_catalog_fields(self, doc_type: str) -> list[FieldDefinition]:
        """Return the list of :class:`FieldDefinition` objects for *doc_type*
        from the on-disk field catalog, or ``[]`` if no matching catalog file
        exists.

        Matching is case-insensitive against the ``doc_type`` value stored in
        each catalog file (or derived from its filename).  Never raises.
        """
        base = self.base_path / "field_catalog"
        if not base.exists():
            return []

        target = doc_type.lower().strip()
        for path in sorted(base.glob("*.json")):
            payload = self._load(path)
            if not payload:
                continue
            # Derive the doc_type the same way _parse_template does
            file_doc_type = str(
                payload.get("doc_type") or path.stem.replace("_fields", "")
            ).strip().lower()
            if file_doc_type != target:
                continue
            # Re-use _parse_template to get a fully-parsed TemplateSchema
            template = self._parse_template(path, payload)
            if template is not None:
                return list(template.fields)

        return []

    # ------------------------------------------------------------------
    # Few-shot examples
    # ------------------------------------------------------------------

    def get_few_shot_examples(self, doc_type: str, limit: int | None = None) -> list[dict]:
        """Return a list of ``{"fields": {...}}`` dicts for *doc_type*.

        - Matches *doc_type* case-insensitively against folder names under
          ``few_shot/``.
        - Results are cached in memory after the first disk read so repeated
          calls within the same process are free.
        - Total serialized size of the returned list is capped at
          ``_FEW_SHOT_MAX_CHARS`` characters to bound token cost.
        - Returns ``[]`` if the folder doesn't exist — never raises.
        """
        key = doc_type.lower().strip()

        if key not in self._few_shot_loaded:
            self._few_shot_loaded.add(key)
            self._few_shot_cache[key] = self._load_few_shot_from_disk(key)

        examples = self._few_shot_cache[key]

        # Apply caller-supplied limit first (e.g. settings.few_shot_examples_per_doc_type)
        if limit is not None:
            examples = examples[:limit]

        # Cap by serialized size to control token cost
        return _cap_by_size(examples, _FEW_SHOT_MAX_CHARS)

    def _load_few_shot_from_disk(self, doc_type_lower: str) -> list[dict]:
        """Scan ``few_shot/`` for a folder whose name matches *doc_type_lower*
        case-insensitively, load every ``*.json`` file, and return only the
        ``fields`` portion of each example (strips metadata/input_text/etc.)."""
        few_shot_dir = self.base_path / "few_shot"
        if not few_shot_dir.exists():
            return []

        # Find the matching sub-folder (case-insensitive)
        target_dir: Path | None = None
        for candidate in few_shot_dir.iterdir():
            if candidate.is_dir() and candidate.name.lower() == doc_type_lower:
                target_dir = candidate
                break

        if target_dir is None:
            return []

        examples: list[dict] = []
        for path in sorted(target_dir.glob("*.json")):
            raw = self._load(path)
            if not isinstance(raw, dict):
                continue
            # Keep only the "output" or "fields" portion — strip metadata
            fields = raw.get("output") or raw.get("fields")
            if isinstance(fields, dict):
                examples.append({"fields": fields})

        return examples

    # ------------------------------------------------------------------
    # Ground-truth lookup
    # ------------------------------------------------------------------

    def get_ground_truth(self, filename_stem: str) -> dict | None:
        """Return the parsed ground-truth JSON for *filename_stem*, or ``None``
        if no matching file exists.  Never raises — a missing file is expected
        for most production uploads."""
        gt_path = self.base_path / "ground_truth" / f"{filename_stem}.json"
        if not gt_path.exists():
            return None
        return self._load(gt_path)

    # ------------------------------------------------------------------
    # Knowledge-base summary
    # ------------------------------------------------------------------

    def sample_summary(self) -> dict[str, object]:
        if not self.base_path.exists():
            return {"documents": 0, "ground_truth": 0, "examples_per_doc_type": 0}

        documents = len(list(self.base_path.glob("documents/*")))
        ground_truth = len(list(self.base_path.glob("ground_truth/*.json")))
        examples = len(list(self.base_path.glob("few_shot/**/*.json")))
        return {
            "documents": documents,
            "ground_truth": ground_truth,
            "examples_per_doc_type": examples,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self, path: Path) -> dict[str, object] | None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _parse_template(self, path: Path, payload: dict[str, object]) -> TemplateSchema | None:
        fields_payload = payload.get("fields")
        if not isinstance(fields_payload, list):
            return None

        fields: list[FieldDefinition] = []
        for f in fields_payload:
            if not isinstance(f, dict):
                continue
            name = str(f.get("name", "")).strip()
            if not name:
                continue
            fields.append(
                FieldDefinition(
                    name=name,
                    type=str(f.get("type", "")).strip() or None,
                    likely_required=bool(f.get("required", f.get("likely_required", False))),
                    validation_rule=str(f.get("validation_rule", "")).strip() or None,
                )
            )

        # doc_type: prefer an explicit field in the JSON, else derive from filename
        # (e.g. "invoice_fields.json" -> "invoice").
        doc_type = str(payload.get("doc_type") or path.stem.replace("_fields", "")).strip()
        if not doc_type:
            return None

        return TemplateSchema(
            doc_type=doc_type,
            description=str(payload.get("description", "")),
            language_support=[DocumentLanguage.EN, DocumentLanguage.TH],
            fields=fields,
        )