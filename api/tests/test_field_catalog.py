"""Tests for the field catalog: exact matching, no aliases, auto-add."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.services.field_catalog import FieldCatalog, normalize_field_name  # noqa: E402


@pytest.fixture
def catalog(tmp_path: Path) -> FieldCatalog:
    kb = tmp_path / "knowledge_base"
    (kb / "field_catalog").mkdir(parents=True)
    (kb / "field_catalog" / "invoice_fields.json").write_text(json.dumps({
        "doc_type": "invoice",
        "fields": [
            {"name": "invoice_number", "type": "string", "required": True},
            {"name": "total_amount", "type": "number", "required": True,
             "alternative_names": ["grand_total", "amount_due"]},
        ],
    }), encoding="utf-8")
    return FieldCatalog(kb)


def test_normalize_is_case_and_spacing_only():
    assert normalize_field_name("  Invoice-Number  ") == "invoice_number"
    assert normalize_field_name("TOTAL   AMOUNT") == "total_amount"


def test_exact_lookup_matches_normalization_only(catalog: FieldCatalog):
    assert catalog.lookup("invoice", "Invoice Number") is not None
    assert catalog.lookup("invoice", "invoice_number") is not None


def test_aliases_are_NEVER_used(catalog: FieldCatalog):
    # 'grand_total' is listed as alternative_names in the file — must NOT match.
    assert catalog.lookup("invoice", "grand_total") is None
    assert catalog.lookup("invoice", "amount_due") is None
    # Synonyms never match either.
    assert catalog.lookup("invoice", "vendor_name") is None


def test_add_fields_appends_and_dedups(catalog: FieldCatalog):
    added = catalog.add_fields("invoice", ["loyalty_points", "loyalty_points", "total_amount"])
    assert added == ["loyalty_points"]

    data = json.loads(
        (catalog.catalog_dir / "invoice_fields.json").read_text(encoding="utf-8")
    )
    new_entry = next(f for f in data["fields"] if f["name"] == "loyalty_points")
    assert new_entry["source"] == "ai_discovered"

    # Now it is lookup-able and no longer "new".
    assert catalog.lookup("invoice", "LOYALTY POINTS") is not None
    assert "loyalty_points" not in catalog.known_names("invoice") or True
    assert "loyalty_points" in catalog.known_names("invoice")


def test_compact_prompt_is_small(catalog: FieldCatalog):
    text = catalog.compact_for_prompt("invoice")
    assert "invoice_number (string, required)" in text
    assert "alternative_names" not in text
