"""Tests for scripts/review_discovered_fields.py (catalog curation helper)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from review_discovered_fields import collect  # noqa: E402


def _write_catalog(tmp_path: Path, filename: str, fields: list[dict]) -> None:
    (tmp_path / filename).write_text(
        json.dumps({"doc_type": "x", "fields": fields}), encoding="utf-8"
    )


def test_collect_lists_only_ai_discovered(tmp_path):
    _write_catalog(tmp_path, "invoice_fields.json", [
        {"name": "invoice_number", "type": "string", "source": "catalog"},
        {"name": "loyalty_points", "type": "string", "source": "ai_discovered"},
    ])
    for missing in ("po_fields.json", "delivery_note_fields.json"):
        (tmp_path / missing).write_text("{}", encoding="utf-8")

    report = collect(tmp_path)
    assert [f["name"] for f in report["invoice"]] == ["loyalty_points"]
    assert report["purchase_order"] == []
    assert report["delivery_note"] == []


def test_collect_flags_suspicious_names(tmp_path):
    _write_catalog(tmp_path, "invoice_fields.json", [
        {"name": "total_amount", "type": "number", "source": "catalog"},
        {"name": "total_amoun", "type": "number", "source": "ai_discovered"},
        {"name": "x" * 31, "type": "string", "source": "ai_discovered"},
        {"name": "total", "type": "string", "source": "ai_discovered"},
        {"name": "clean_field", "type": "string", "source": "ai_discovered"},
    ])
    for missing in ("po_fields.json", "delivery_note_fields.json"):
        (tmp_path / missing).write_text("{}", encoding="utf-8")

    by_name = {f["name"]: f for f in collect(tmp_path)["invoice"]}
    assert any(fl.startswith("NEAR-DUP") for fl in by_name["total_amoun"]["flags"])
    assert "LONG>30" in by_name["x" * 31]["flags"]
    assert "GENERIC" in by_name["total"]["flags"]
    assert by_name["clean_field"]["flags"] == []


def test_collect_handles_missing_and_broken_files(tmp_path):
    report = collect(tmp_path)  # nothing on disk
    assert report == {"invoice": [], "purchase_order": [], "delivery_note": []}

    (tmp_path / "invoice_fields.json").write_text("not json{{", encoding="utf-8")
    report = collect(tmp_path)
    assert report["invoice"] == []
