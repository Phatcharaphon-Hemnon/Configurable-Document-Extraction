"""Acceptance-policy regression (synthetic fixtures, not real refs).

Covers: blank labels-as-values, invalid dates/amounts, unsupported currency,
buyer/supplier roles, printed zeros + repeated values, duplicate columns,
wrong-row evidence, invalid table structures, and legacy loading labels.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.schemas.documents import ExtractedField, ExtractedTable  # noqa: E402
from app.services.acceptance import accept_page  # noqa: E402
from app.services.field_catalog import FieldCatalog  # noqa: E402


def _catalog(tmp_path: Path) -> FieldCatalog:
    kb = tmp_path / "kb"
    (kb / "field_catalog").mkdir(parents=True)
    (kb / "field_catalog" / "invoice_fields.json").write_text(json.dumps({
        "doc_type": "invoice",
        "fields": [
            {"name": "invoice_number", "type": "string", "required": True},
            {"name": "invoice_date", "type": "date", "required": True},
            {"name": "seller_name", "type": "string", "required": True},
            {"name": "total_amount", "type": "number", "required": True},
            {"name": "seller_address", "type": "string", "required": False},
            {"name": "bill_to_name", "type": "string", "required": False},
            {"name": "bill_to_address", "type": "string", "required": False},
            {"name": "currency", "type": "string", "required": False},
        ],
    }), encoding="utf-8")
    (kb / "field_catalog" / "po_fields.json").write_text(json.dumps({
        "doc_type": "purchase_order",
        "fields": [
            {"name": "po_number", "type": "string", "required": True},
            {"name": "supplier_name", "type": "string", "required": False},
            {"name": "buyer_name", "type": "string", "required": False},
        ],
    }), encoding="utf-8")
    return FieldCatalog(kb)


def _f(name, value, span, conf=0.95):
    return ExtractedField(name=name, value=value, confidence=conf, source_span=span)


def test_blank_labels_rejected_not_accepted(tmp_path):
    cat = _catalog(tmp_path)
    page = "Client name:\nAddress:\nTotal\nTHB 0.00"
    fields = [
        _f("seller_name", "Client name:", "Client name:"),
        _f("total_amount", "ยอดรวม", "ยอดรวม"),
        _f("bill_to_address", "ที่อยู่", "ที่อยู่"),
    ]
    accepted, _, rejected, _, coverage = accept_page(
        "invoice", page_number=1, fields=fields, tables=[], page_text=page,
        blocks=[], catalog=cat,
    )
    assert accepted == []
    assert len(rejected) == 3
    assert coverage == 0.0
    locs = {r.location for r in rejected}
    assert {"seller_name", "total_amount", "bill_to_address"} <= locs


def test_printed_thb_accepted_when_explicit(tmp_path):
    cat = _catalog(tmp_path)
    page = "Currency: THB\nTotal: 0.00"
    fields = [_f("currency", "THB", "Currency: THB")]
    accepted, _, rejected, _, _ = accept_page(
        "invoice", page_number=1, fields=fields, tables=[], page_text=page,
        blocks=[], catalog=cat,
    )
    assert [f.name for f in accepted] == ["currency"]
    assert rejected == []


def test_invalid_date_and_amount_rejected(tmp_path):
    cat = _catalog(tmp_path)
    page = "Date: 253 KINGS ROAD\nTotal: ยอดรวม"
    fields = [
        _f("invoice_date", "253 KINGS ROAD", "Date: 253 KINGS ROAD"),
        _f("total_amount", "ยอดรวม", "Total: ยอดรวม"),
    ]
    accepted, _, rejected, _, _ = accept_page(
        "invoice", page_number=1, fields=fields, tables=[], page_text=page,
        blocks=[], catalog=cat,
    )
    assert accepted == []
    assert any("Unparseable date" in r.rejection_reason for r in rejected)
    assert any(r.location == "total_amount" for r in rejected)


def test_unsupported_currency_rejected(tmp_path):
    cat = _catalog(tmp_path)
    page = "Total: 100\nNo currency printed"
    fields = [_f("currency", "THB", "Total: 100")]
    accepted, _, rejected, _, _ = accept_page(
        "invoice", page_number=1, fields=fields, tables=[], page_text=page,
        blocks=[], catalog=cat,
    )
    assert accepted == []
    assert rejected and "currency" in rejected[0].location.lower() or rejected[0].location == "currency"


def test_buyer_supplier_roles_require_evidence(tmp_path):
    cat = _catalog(tmp_path)
    page = "Customer Name: Paula Parente\nOrder 10256"
    fields = [
        _f("buyer_name", "Paula Parente", "Customer Name: Paula Parente"),
        _f("supplier_name", "Paula Parente", "Customer Name: Paula Parente"),
    ]
    accepted, _, rejected, _, _ = accept_page(
        "purchase_order", page_number=1, fields=fields, tables=[], page_text=page,
        blocks=[], catalog=cat,
    )
    names = {f.name for f in accepted}
    assert "buyer_name" in names
    assert "supplier_name" not in names
    assert any(r.location == "supplier_name" and "role" in r.rejection_reason.lower() for r in rejected)


def test_zeros_and_repeated_values_preserved(tmp_path):
    cat = _catalog(tmp_path)
    page = "Invoice No: 000123\nTotal: 0.00\nPaid: 0.00"
    fields = [
        _f("invoice_number", "000123", "Invoice No: 000123"),
        _f("total_amount", 0.0, "Total: 0.00"),
    ]
    accepted, _, rejected, _, _ = accept_page(
        "invoice", page_number=1, fields=fields, tables=[], page_text=page,
        blocks=[], catalog=cat,
    )
    assert {f.name for f in accepted} == {"invoice_number", "total_amount"}
    assert rejected == []
    assert accepted[0].value == "000123"


def test_duplicate_columns_get_positional_keys(tmp_path):
    cat = _catalog(tmp_path)
    page = "A | B | B\n1 | 2 | 3"
    table = ExtractedTable(
        name="line_items",
        columns=[
            {"key": "s_price", "label": "S/PRICE"},
            {"key": "S/PRICE", "label": "S/PRICE"},
            {"key": "s_price", "label": "S/PRICE"},
        ],
        rows=[[  # use distinct spans per cell so each binds to its region
            {"column": "s_price", "value": "1", "confidence": 0.9, "source_span": "A | B"},
            {"column": "S/PRICE", "value": "2", "confidence": 0.9, "source_span": "B\n1 | 2"},
            {"column": "s_price", "value": "3", "confidence": 0.9, "source_span": "| 3"},
        ]],
    )
    # Fix columns to unique first for this unit check via accept_page normalization.
    accepted, tables, rejected, _, _ = accept_page(
        "invoice", page_number=1, fields=[], tables=[table], page_text=page,
        blocks=[], catalog=cat,
    )
    # Duplicate input keys must not merge: either normalized to unique keys or
    # rejected as structurally invalid — never silently merged.
    if tables:
        keys = [c.key for c in tables[0].columns]
        assert len(keys) == len(set(keys))
    else:
        assert rejected


def test_wrong_row_and_invalid_structures_rejected(tmp_path):
    cat = _catalog(tmp_path)
    page = "Item A 10\nTotal 100"
    bad = ExtractedTable(
        name="line_items",
        columns=[{"key": "a", "label": "A"}, {"key": "b", "label": "B"}],
        rows=[[
            {"column": "a", "value": "Item A", "confidence": 0.9, "source_span": "Item A 10"},
            {"column": "c_unknown", "value": "10", "confidence": 0.9, "source_span": "Item A 10"},
        ]],
    )
    _, tables, rejected, _, _ = accept_page(
        "invoice", page_number=1, fields=[], tables=[bad], page_text=page,
        blocks=[], catalog=cat,
    )
    assert tables == []
    assert rejected

    placeholder = ExtractedTable(
        name="line_items",
        columns=[{"key": "a", "label": "A"}],
        rows=[[{"column": "a", "value": "ไม่มีข้อมูล", "confidence": 0.9, "source_span": "ไม่มีข้อมูล"}]],
    )
    _, tables2, rejected2, _, _ = accept_page(
        "invoice", page_number=1, fields=[], tables=[placeholder],
        page_text="ไม่มีข้อมูล", blocks=[], catalog=cat,
    )
    assert tables2 == []
    assert rejected2


def test_legacy_records_default_unevaluated():
    from app.schemas.documents import ExtractionResult

    legacy = {
        "doc_type": "invoice",
        "fields": [{"name": "invoice_number", "value": "INV-1", "confidence": 0.9, "source_span": "No: INV-1"}],
        "validation_errors": [],
        "needs_review": False,
    }
    result = ExtractionResult.model_validate(legacy)
    assert result.acceptance_status == "unevaluated"
    assert result.rejected_candidates == []
    assert result.review_issues == []
    assert result.fields[0].acceptance == "unevaluated"
