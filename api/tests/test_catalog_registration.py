"""Tests for automatic catalog registration of AI-discovered fields.

Covers the shared register_discovered_fields helper (used by BOTH the
in-process pipeline and the Temporal worker) plus the Temporal
extract_activity wiring that previously dropped new names silently.
All tmp-KB, no LLM calls.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.schemas.documents import ExtractedField  # noqa: E402
from app.services.field_catalog import (  # noqa: E402
    FieldCatalog,
    min_new_field_confidence,
    register_discovered_fields,
    skip_reason,
)


def _catalog(tmp_path: Path) -> FieldCatalog:
    kb = tmp_path / "kb"
    (kb / "field_catalog").mkdir(parents=True)
    (kb / "field_catalog" / "invoice_fields.json").write_text(json.dumps({
        "doc_type": "invoice",
        "fields": [{"name": "invoice_number", "type": "string", "required": True}],
    }), encoding="utf-8")
    return FieldCatalog(kb)


def _field(name: str, value: object = "v", confidence: float = 0.9) -> ExtractedField:
    # source_span quotes the value itself so it is verifiable against any
    # document text containing that value (evidence gate: Issue 9).
    return ExtractedField(name=name, value=value, confidence=confidence,
                          source_span=f"{value}")


def test_known_field_not_marked_new(tmp_path):
    outcome = register_discovered_fields(
        _catalog(tmp_path), "invoice", [_field("invoice_number", "INV-1")])
    assert outcome.fields[0].is_new_field is False
    assert outcome.added == []
    assert outcome.skipped == []


def test_new_field_registered_with_reasons_for_skips(tmp_path):
    catalog = _catalog(tmp_path)
    outcome = register_discovered_fields(catalog, "invoice", [
        _field("loyalty_earned", "250 PTS", 0.9),   # registers
        _field("weak_guess", "x", 0.3),             # low confidence
        _field("junk_note", "N/A", 0.9),            # placeholder value
        _field("bad name!", "x", 0.9),              # not snake_case
    ])
    assert outcome.added == ["loyalty_earned"]
    assert [f.name for f in outcome.fields if f.is_new_field] == [
        "loyalty_earned", "weak_guess", "junk_note", "bad name!"]
    reasons = {s["name"]: s["reason"] for s in outcome.skipped}
    assert set(reasons) == {"weak_guess", "junk_note", "bad name!"}
    assert "confidence" in reasons["weak_guess"]
    assert "placeholder" in reasons["junk_note"]
    assert "snake_case" in reasons["bad name!"]

    # Persisted with the ai_discovered source marker.
    data = json.loads((tmp_path / "kb" / "field_catalog" / "invoice_fields.json")
                      .read_text(encoding="utf-8"))
    discovered = [f for f in data["fields"] if f.get("source") == "ai_discovered"]
    assert [f["name"] for f in discovered] == ["loyalty_earned"]


def test_rerun_is_idempotent(tmp_path):
    catalog = _catalog(tmp_path)
    fields = [_field("loyalty_earned", "250 PTS", 0.9)]
    first = register_discovered_fields(catalog, "invoice", fields)
    assert first.added == ["loyalty_earned"]
    second = register_discovered_fields(catalog, "invoice", first.fields)
    assert second.added == []
    assert second.skipped == []
    assert second.fields[0].is_new_field is False  # now a known name


def test_threshold_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("NEW_FIELD_MIN_CONFIDENCE", "0.95")
    assert min_new_field_confidence() == pytest.approx(0.95)
    outcome = register_discovered_fields(
        _catalog(tmp_path), "invoice", [_field("loyalty_earned", "250 PTS", 0.9)])
    assert outcome.added == []
    assert outcome.skipped[0]["reason"].startswith("low confidence")


def test_invalid_threshold_env_falls_back(monkeypatch):
    monkeypatch.setenv("NEW_FIELD_MIN_CONFIDENCE", "nonsense")
    assert min_new_field_confidence() == pytest.approx(0.8)
    assert skip_reason("ok_name", "v", 0.9) is None


@pytest.mark.asyncio
async def test_temporal_extract_activity_registers_new_fields(tmp_path, monkeypatch):
    from app.temporal import activities

    catalog = _catalog(tmp_path)
    monkeypatch.setattr(activities, "_catalog", lambda: catalog)

    fake_extractor = MagicMock()
    fake_extractor.extract = AsyncMock(return_value=(
        [_field("invoice_number", "INV-1", 0.95),
         _field("loyalty_earned", "250 PTS", 0.9)],
        [],  # extract() never reports new names itself (see activities note)
    ))
    monkeypatch.setattr("app.agents.extractors.build_extractors",
                        lambda settings, cat: {"invoice": fake_extractor})

    result = await activities.extract_activity("scan.png", "Loyalty Earned 250 PTS", "invoice")

    names = {f["name"]: f for f in result["fields"]}
    assert names["loyalty_earned"]["is_new_field"] is True
    data = json.loads((tmp_path / "kb" / "field_catalog" / "invoice_fields.json")
                      .read_text(encoding="utf-8"))
    assert "loyalty_earned" in {f["name"] for f in data["fields"]
                                if f.get("source") == "ai_discovered"}
