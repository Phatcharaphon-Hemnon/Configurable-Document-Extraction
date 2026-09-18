"""Fields-only diagnostic schema variant (tables-nesting falsification).

Temporary diagnostic behind a default-ON flag: production default keeps
the full ExtractionResponseSchema untouched. Offline only — no inference.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from app.agents.extractors import _build_prompt, tables_enabled  # noqa: E402
from app.schemas.llm_schemas import (  # noqa: E402
    ExtractionFieldsOnlySchema,
    ExtractionResponseSchema,
)


def test_default_schema_keeps_tables_block():
    props = set(ExtractionResponseSchema.model_json_schema()["properties"])
    assert {"fields", "tables"} <= props


def test_variant_is_fields_only_strict():
    props = set(ExtractionFieldsOnlySchema.model_json_schema()["properties"])
    assert "fields" in props and "tables" not in props
    ok = ExtractionFieldsOnlySchema.model_validate({
        "fields": [{"name": "total_amount", "value": "42.40",
                    "confidence": 0.9, "source_span": "TOTAL 42.40"}]})
    assert ok.fields[0].name == "total_amount"
    with pytest.raises(ValidationError):
        ExtractionFieldsOnlySchema.model_validate({"fields": [], "tables": []})


def test_tables_enabled_defaults_on():
    assert tables_enabled(SimpleNamespace()) is True
    assert tables_enabled(SimpleNamespace(extraction_tables_enabled=True)) is True
    assert tables_enabled(SimpleNamespace(extraction_tables_enabled=False)) is False


def test_build_prompt_tables_rule_follows_flag():
    full = _build_prompt("invoice", "CAT", "sometext", None)
    assert "Return tables separately" in full
    narrow = _build_prompt("invoice", "CAT", "sometext", None, include_tables=False)
    assert "Return tables separately" not in narrow
    assert "sometext" in narrow
