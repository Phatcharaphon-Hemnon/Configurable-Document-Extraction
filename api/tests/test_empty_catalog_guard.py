"""Empty-catalog guard regression (X51008042779.jpg diagnosis).

The runtime KB once held a single non-required invoice field and no PO/DN
catalogs at all: extractor prompts ran unguided, required/type checks were
vacuous, and coverage stuck at 1.0 with zero accepted fields. The service
must log a loud warning at startup when any doc type has no catalog.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.core.config import Settings  # noqa: E402
from app.services.extraction_service import DocumentExtractionService  # noqa: E402

_LOGGER = "app.services.extraction_service"


def _settings(kb: Path) -> Settings:
    s = Settings()
    s.knowledge_base_path = str(kb)
    return s


def _seeded_settings(tmp_path: Path) -> Settings:
    kb = tmp_path / "kb"
    (kb / "field_catalog").mkdir(parents=True)
    for doc_type, name in (("invoice", "invoice_number"), ("purchase_order", "po_number"),
                           ("delivery_note", "delivery_note_number")):
        (kb / "field_catalog" / f"{doc_type}_fields.json").write_text(json.dumps({
            "doc_type": doc_type,
            "fields": [{"name": name, "type": "string", "required": True}],
        }), encoding="utf-8")
    # PO/DN loader reads po_fields.json / delivery_note_fields.json.
    (kb / "field_catalog" / "po_fields.json").write_text(json.dumps({
        "doc_type": "po",
        "fields": [{"name": "po_number", "type": "string", "required": True}],
    }), encoding="utf-8")
    (kb / "field_catalog" / "delivery_note_fields.json").write_text(json.dumps({
        "doc_type": "delivery_note",
        "fields": [{"name": "delivery_note_number", "type": "string", "required": True}],
    }), encoding="utf-8")
    return _settings(kb)


def test_empty_catalog_logs_warning_per_doc_type(tmp_path, caplog):
    kb = tmp_path / "empty_kb"  # no field_catalog dir at all
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        DocumentExtractionService(settings=_settings(kb))
    warned = {r.message for r in caplog.records if "EMPTY" in r.message}
    assert any("invoice" in m for m in warned)
    assert any("purchase_order" in m for m in warned)
    assert any("delivery_note" in m for m in warned)


def test_seeded_catalog_is_silent(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        DocumentExtractionService(settings=_seeded_settings(tmp_path))
    assert not [r for r in caplog.records if "EMPTY" in r.message]


def test_guard_never_raises_on_broken_catalog(tmp_path, caplog):
    kb = tmp_path / "kb"
    (kb / "field_catalog").mkdir(parents=True)
    (kb / "field_catalog" / "invoice_fields.json").write_text("not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        DocumentExtractionService(settings=_settings(kb))
    # Corrupt file reads as empty → warning, never an exception.
    assert any("EMPTY" in r.message for r in caplog.records)
