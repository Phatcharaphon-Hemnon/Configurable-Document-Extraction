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
import os
import re
import threading
from pathlib import Path

from app.core.security import check_evidence
from app.schemas.documents import DocType, ExtractedField, FieldDefinition, RegistrationOutcome

# Catalog file names use the legacy short keys.
_FILE_BY_DOC_TYPE: dict[DocType, str] = {
    "invoice": "invoice_fields.json",
    "purchase_order": "po_fields.json",
    "delivery_note": "delivery_note_fields.json",
}

# Values that mean "the field is absent" — never extracted, never registered.
PLACEHOLDER_VALUES: frozenset[str] = frozenset({
    "", "n/a", "n.a", "n.a.", "na", "-", "--", "—", "–", "null", "none", "nil",
    "not available", "not applicable", "not answerable", "unanswerable",
    "no answer", "not provided", "not stated", "not found", "unknown",
    "no value", "?", "??", "tbd", "blank", "empty",
})

# A field name must look like a clean snake_case identifier to be catalog-worthy.
_SANE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")

# Minimum confidence for an AI-discovered field to be written into the catalog.
NEW_FIELD_MIN_CONFIDENCE = 0.6

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


def is_placeholder_value(value: object) -> bool:
    """True when a value is a placeholder meaning 'absent' (e.g. 'N/A')."""
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower().replace("_", " ")
    return normalized in PLACEHOLDER_VALUES


def is_sane_field_name(name: str) -> bool:
    """True when the name is a clean snake_case identifier worth keeping."""
    return bool(_SANE_NAME_RE.match(name))


def is_registerable_new_field(name: str, value: object, confidence: float) -> bool:
    """A new field may only be auto-registered when it is REAL:
    non-placeholder value, sane snake_case name, sufficient confidence."""
    return (
        not is_placeholder_value(value)
        and confidence >= min_new_field_confidence()
        and is_sane_field_name(name)
    )


class FieldCatalog:
    def __init__(self, knowledge_base_dir: Path) -> None:
        self.catalog_dir = knowledge_base_dir / "field_catalog"
        self._cache: dict[DocType, list[FieldDefinition]] = {}
        self._cache_mtimes: dict[DocType, float] = {}

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def _path_for(self, doc_type: DocType) -> Path:
        return self.catalog_dir / _FILE_BY_DOC_TYPE[doc_type]

    def _get_cache_mtime(self, doc_type: DocType) -> float:
        """Get file modification time for cache invalidation."""
        path = self._path_for(doc_type)
        if path.exists():
            return path.stat().st_mtime
        return 0.0

    def get_fields(self, doc_type: DocType) -> list[FieldDefinition]:
        """Get fields with caching. Cache is invalidated when file changes."""
        # Check if cache is valid
        current_mtime = self._get_cache_mtime(doc_type)
        if doc_type in self._cache and self._cache_mtimes.get(doc_type) == current_mtime:
            return self._cache[doc_type]

        # Cache miss or invalid - read from disk
        path = self._path_for(doc_type)
        if not path.exists():
            self._cache[doc_type] = []
            self._cache_mtimes[doc_type] = 0.0
            return []

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._cache[doc_type] = []
            self._cache_mtimes[doc_type] = 0.0
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

        # Update cache
        self._cache[doc_type] = fields
        self._cache_mtimes[doc_type] = current_mtime

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
                if normalized in existing or not is_sane_field_name(normalized):
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
                # Invalidate cache after write
                if doc_type in self._cache:
                    del self._cache[doc_type]
                    del self._cache_mtimes[doc_type]
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


def min_new_field_confidence() -> float:
    try:
        return max(0, min(1, float(os.getenv("NEW_FIELD_MIN_CONFIDENCE", str(NEW_FIELD_MIN_CONFIDENCE)))))
    except ValueError:
        return NEW_FIELD_MIN_CONFIDENCE


def skip_reason(name: str, value: object, confidence: float) -> str | None:
    if is_placeholder_value(value):
        return "placeholder value"
    if not is_sane_field_name(name):
        return "invalid snake_case name"
    if confidence < min_new_field_confidence():
        return "low confidence"
    return None


def register_discovered_fields(catalog: FieldCatalog, doc_type: DocType,
                               fields: list[ExtractedField], document_text: str | None = None) -> RegistrationOutcome:
    known = catalog.known_names(doc_type)
    outcome = RegistrationOutcome(fields=[f.model_copy(update={"is_new_field": normalize_field_name(f.name) not in known}) for f in fields])
    eligible = []
    for field in outcome.fields:
        if not field.is_new_field:
            continue
        reason = skip_reason(field.name, field.value, field.confidence)
        if reason is None and document_text is not None:
            reason = check_evidence(field.name, field.value, field.source_span, document_text)
        if reason:
            outcome.skipped.append({"name": field.name, "reason": reason})
        else:
            eligible.append(field.name)
    outcome.added = catalog.add_fields(doc_type, eligible)
    return outcome
