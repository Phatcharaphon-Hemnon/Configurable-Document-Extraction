"""Field catalog: single source of truth for field names per document type.

Rules (project spec):
- Field-name matching is EXACT after basic normalization (trim, lowercase,
  whitespace/hyphens → underscore). Aliases/synonyms are NEVER used.
- When the AI extracts a labeled field whose name is not in the catalog,
  the pipeline ADDS it to the catalog file (source="ai_discovered").
- Catalog writes are atomic (tmp file + rename) and deduplicated.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from app.schemas.documents import DocType, FieldDefinition

# Catalog file names use the legacy short keys.
_FILE_BY_DOC_TYPE: dict[DocType, str] = {
    "invoice": "invoice_fields.json",
    "purchase_order": "po_fields.json",
    "delivery_note": "delivery_note_fields.json",
}

_lock = threading.Lock()


def normalize_field_name(name: str) -> str:
    """Canonical form of a field name: trim, lowercase, whitespace/hyphens
    to underscores, collapse repeats. This is NOT synonym mapping —
    'Total Amount' and 'total_amount' match; 'grand_total' does NOT match
    'total_amount'."""
    s = name.strip().lower()
    s = re.sub(r"[\s\-]+", "_", s)
    s = re.sub(r"_+", "_", s)
    return s.strip("_")


class FieldCatalog:
    def __init__(self, knowledge_base_dir: Path) -> None:
        self.catalog_dir = knowledge_base_dir / "field_catalog"

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def _path_for(self, doc_type: DocType) -> Path:
        return self.catalog_dir / _FILE_BY_DOC_TYPE[doc_type]

    def get_fields(self, doc_type: DocType) -> list[FieldDefinition]:
        path = self._path_for(doc_type)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        fields: list[FieldDefinition] = []
        for entry in data.get("fields", []):
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            fields.append(
                FieldDefinition(
                    name=str(entry["name"]),
                    description=entry.get("validation_rule") or entry.get("description"),
                    type=entry.get("type"),
                    required=bool(entry.get("required", False)),
                    source=str(entry.get("source", "catalog")),
                )
            )
        return fields

    def get_field_names(self, doc_type: DocType) -> list[str]:
        return [f.name for f in self.get_fields(doc_type)]

    def lookup(self, doc_type: DocType, name: str) -> FieldDefinition | None:
        """Exact (normalized) name lookup. No alias/synonym fallback."""
        wanted = normalize_field_name(name)
        for field in self.get_fields(doc_type):
            if normalize_field_name(field.name) == wanted:
                return field
        return None

    def known_names(self, doc_type: DocType) -> set[str]:
        return {normalize_field_name(n) for n in self.get_field_names(doc_type)}

    # ------------------------------------------------------------------
    # Write (auto-register AI-discovered fields)
    # ------------------------------------------------------------------

    def add_fields(self, doc_type: DocType, names: list[str]) -> list[str]:
        """Append new field entries to the catalog file. Returns the names
        that were actually added (dedup, atomic write)."""
        new_normalized = [normalize_field_name(n) for n in names if n and n.strip()]
        if not new_normalized:
            return []

        with _lock:
            path = self._path_for(doc_type)
            data: dict = {}
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    data = {}
            data.setdefault("doc_type", doc_type)
            data.setdefault("description", "")
            fields = data.setdefault("fields", [])

            existing = {
                normalize_field_name(f.get("name", ""))
                for f in fields
                if isinstance(f, dict)
            }

            added: list[str] = []
            for original, normalized in zip(names, new_normalized):
                if normalized in existing:
                    continue
                fields.append(
                    {
                        "name": normalized,
                        "type": "string",
                        "required": False,
                        "validation_rule": "Discovered automatically from an uploaded document",
                        "source": "ai_discovered",
                    }
                )
                existing.add(normalized)
                added.append(normalized)

            if added:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp.replace(path)
            return added

    # ------------------------------------------------------------------
    # Prompt helper (token-minimized)
    # ------------------------------------------------------------------

    def compact_for_prompt(self, doc_type: DocType) -> str:
        """Compact catalog representation for LLM prompts: one line per field,
        name + type + required marker only. Minimizes token usage."""
        lines = [
            f"{f.name} ({f.type or 'string'}{', required' if f.required else ''})"
            for f in self.get_fields(doc_type)
        ]
        return "\n".join(lines) if lines else "(catalog empty — extract clearly labeled fields)"
