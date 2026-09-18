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


# --- v1.1.0: invoice letterhead supplier + placeholder cell relaxation ---


def test_invoice_letterhead_supplier_accepted_without_role_label(tmp_path):
    """Receipt header IS the seller: top-of-page position satisfies supplier
    fields on invoices (info issue, no forced review)."""
    cat = _catalog(tmp_path)
    page = (
        "RESTORAN WAN SHENG\n002043319-W\nNo.2, Jalan Temenggung 19/9\n"
        "Tax Invoice\nINV No.: 1213543\nDate: 27-06-2018\nTOTAL: 2.30"
    )
    fields = [
        _f("seller_name", "RESTORAN WAN SHENG", "RESTORAN WAN SHENG"),
        _f("seller_address", "No.2, Jalan Temenggung 19/9", "No.2, Jalan Temenggung 19/9"),
        _f("invoice_number", "1213543", "INV No.: 1213543"),
    ]
    accepted, _, rejected, issues, coverage = accept_page(
        "invoice", page_number=1, fields=fields, tables=[], page_text=page,
        blocks=[], catalog=cat,
    )
    names = {f.name for f in accepted}
    assert {"seller_name", "seller_address", "invoice_number"} <= names
    assert rejected == []
    # required = invoice_number, invoice_date, seller_name, total_amount → 2/4
    assert coverage == 0.5
    assert any(
        i.severity == "info" and "letterhead" in i.explanation and i.target == "field:seller_name"
        for i in issues
    )


def test_invoice_supplier_midpage_without_role_still_rejected(tmp_path):
    """No role label AND not in the header region → still rejected."""
    cat = _catalog(tmp_path)
    filler = "\n".join(f"line {i} padding text" for i in range(60))
    page = f"Tax Invoice\n{filler}\nACME Tools Ltd"
    fields = [_f("seller_name", "ACME Tools Ltd", "ACME Tools Ltd")]
    accepted, _, rejected, _, _ = accept_page(
        "invoice", page_number=1, fields=fields, tables=[], page_text=page,
        blocks=[], catalog=cat,
    )
    assert accepted == []
    assert any("role" in r.rejection_reason.lower() for r in rejected)


def test_po_letterhead_supplier_not_relaxed(tmp_path):
    """A purchase order's letterhead is the BUYER — header position must
    never satisfy a supplier field on POs (scope: invoice only)."""
    cat = _catalog(tmp_path)
    page = "ACME BUYER CORP\nPO No: 1"
    fields = [_f("supplier_name", "ACME BUYER CORP", "ACME BUYER CORP")]
    accepted, _, rejected, _, _ = accept_page(
        "purchase_order", page_number=1, fields=fields, tables=[], page_text=page,
        blocks=[], catalog=cat,
    )
    assert accepted == []
    assert any("role" in r.rejection_reason.lower() for r in rejected)


def _receipt_table(tax_row0="ไม่มีข้อมูล", tax_row1="ไม่มีข้อมูล", row1_total_span="0.20 ZRL"):
    def cell(col, value, span, conf=0.9):
        return {"column": col, "value": value, "confidence": conf, "source_span": span}

    def tax_cell(tax_value):
        # Placeholder cells carry no span (absent data); a printed tax
        # amount needs verbatim span evidence like any real value.
        if tax_value == "ไม่มีข้อมูล":
            return cell("tax", tax_value, None, 0.5)
        return cell("tax", tax_value, tax_value, 0.9)

    return ExtractedTable(
        name="line_items",
        columns=[
            {"key": "description", "label": "Description"},
            {"key": "quantity", "label": "Qty"},
            {"key": "unit_price", "label": "U.Price"},
            {"key": "total_price", "label": "Total"},
            {"key": "tax", "label": "Tax"},
        ],
        rows=[
            [
                cell("description", "Cham (B)", "Cham (B) 1"),
                cell("quantity", 1, "1 x"),
                cell("unit_price", 2.10, "x 2.10"),
                cell("total_price", 2.10, "2.10 ZRL"),
                tax_cell(tax_row0),
            ],
            [
                cell("description", "Take Away", "Take Away 1"),
                cell("quantity", 1, "1 x"),
                cell("unit_price", 0.20, "x 0.20"),
                cell("total_price", 0.20, row1_total_span),
                tax_cell(tax_row1),
            ],
        ],
    )


def test_placeholder_tax_cells_do_not_withhold_rows(tmp_path):
    """v1.1.0: placeholder cells mean absent data — rows keep their grounded
    cells; a fully-placeholder column is dropped from the accepted table."""
    cat = _catalog(tmp_path)
    page = "Cham (B) 1 x 2.10 2.10 ZRL\nTake Away 1 x 0.20 0.20 ZRL\nTOTAL: 2.30"
    _, tables, rejected, issues, _ = accept_page(
        "invoice", page_number=1, fields=[], tables=[_receipt_table()],
        page_text=page, blocks=[], catalog=cat,
    )
    assert len(tables) == 1
    keys = [c.key for c in tables[0].columns]
    assert "tax" not in keys  # all tax cells placeholder → column dropped
    assert len(tables[0].rows) == 2
    assert all(len(row) == 4 for row in tables[0].rows)
    assert any("placeholder cell omitted" in r.rejection_reason for r in rejected)
    assert not any("row withheld" in r.rejection_reason for r in rejected)
    assert not any("table withheld" in r.rejection_reason for r in rejected)
    assert any(i.severity == "info" and "column 'tax' omitted" in i.explanation for i in issues)


def test_mixed_placeholder_tax_keeps_column_and_row(tmp_path):
    """One row placeholder tax, one row printed zero: column survives, the
    placeholder row is kept without the tax cell, and the validator's
    structural gate tolerates the placeholder-omitted cell. Runs the full
    validate_detailed pass once (raw extractor output), like the pipeline."""
    from app.agents.validator import ValidatorAgent

    cat = _catalog(tmp_path)
    page = (
        "RESTORAN WAN SHENG\nTax Invoice\nINV No.: 1213543\nDate: 27-06-2018\n"
        "Cham (B) 1 x 2.10 2.10 ZRL\nTake Away 1 x 0.20 0.20 0.00\nTOTAL: 2.30"
    )
    table = _receipt_table(tax_row1="0.00", row1_total_span="0.20 0.00")
    fields = [
        _f("invoice_number", "1213543", "INV No.: 1213543"),
        _f("invoice_date", "27-06-2018", "27-06-2018"),
        _f("seller_name", "RESTORAN WAN SHENG", "RESTORAN WAN SHENG"),
        _f("total_amount", 2.30, "TOTAL: 2.30"),
    ]
    validator = ValidatorAgent(cat)
    errors, completeness, needs_review, vfields, vtables, rej, iss = validator.validate_detailed(
        "invoice", fields, document_text=page, tables=[table],
    )
    structural = [e for e in errors if "columns" in e or "empty row" in e]
    assert structural == []
    assert len(vtables) == 1
    keys = [c.key for c in vtables[0].columns]
    assert "tax" in keys  # row 1 has a real tax cell → column kept
    assert [len(row) for row in vtables[0].rows] == [4, 5]  # row 0 omitted tax
    # All four required invoice fields accepted (seller via letterhead).
    assert completeness == 1.0
    assert needs_review is False
