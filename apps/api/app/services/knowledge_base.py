"""File-backed knowledge base: few-shot examples, ground truth, catalog access."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.schemas.documents import DOC_TYPES, DocType
from app.services.field_catalog import FieldCatalog

logger = logging.getLogger(__name__)

_FEW_SHOT_MAX_CHARS = 2000
_FEW_SHOT_DIR_BY_DOC_TYPE: dict[DocType, str] = {
    "invoice": "invoice",
    "purchase_order": "po",
    "delivery_note": "delivery_note",
}


def _cap_by_size(examples: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Keep at most *limit* examples AND at most ~_FEW_SHOT_MAX_CHARS of JSON
    total (token control)."""
    kept: list[dict[str, Any]] = []
    budget = _FEW_SHOT_MAX_CHARS
    for example in examples[:limit]:
        size = len(json.dumps(example, ensure_ascii=False))
        if size > budget:
            break
        kept.append(example)
        budget -= size
    return kept


class KnowledgeBaseRepository:
    def __init__(self, base_path: Path) -> None:
        self.base_path = base_path
        self.catalog = FieldCatalog(base_path)

    # ------------------------------------------------------------------
    # Few-shot examples (optional, token-gated)
    # ------------------------------------------------------------------

    def get_few_shot_examples(self, doc_type: DocType, limit: int = 2) -> list[dict[str, Any]]:
        folder = self.base_path / "few_shot" / _FEW_SHOT_DIR_BY_DOC_TYPE[doc_type]
        if not folder.exists():
            return []
        examples: list[dict[str, Any]] = []
        for path in sorted(folder.glob("*.json")):
            try:
                examples.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return _cap_by_size(examples, limit)

    # ------------------------------------------------------------------
    # Ground truth (auto-eval)
    # ------------------------------------------------------------------

    def get_ground_truth(self, filename_stem: str) -> dict[str, Any] | None:
        path = self.base_path / "ground_truth" / f"{filename_stem}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    # ------------------------------------------------------------------
    # Templates API
    # ------------------------------------------------------------------

    def list_templates(self) -> list[dict[str, Any]]:
        templates = []
        for doc_type in DOC_TYPES:
            fields = self.catalog.get_fields(doc_type)
            templates.append(
                {
                    "doc_type": doc_type,
                    "description": f"Fixed-schema {doc_type.replace('_', ' ')}",
                    "field_count": len(fields),
                    "fields": [f.model_dump() for f in fields],
                }
            )
        return templates
