"""Evidence checks for dynamic source-language tables and legacy rows."""

import pytest

from app.agents.validator import ValidatorAgent
from app.schemas.documents import ExtractedField, ExtractedTable
from app.services.field_catalog import FieldCatalog


def test_thai_discount_table_checks_every_cell(tmp_path):
    table = ExtractedTable(
        name="line_items",
        columns=[
            {"key": f"c{i}", "label": label}
            for i, label in enumerate(
                ["ลำดับ", "รหัสสินค้า", "รายการสินค้า", "จำนวน", "ราคาต่อหน่วย", "รวม", "ส่วนลด%", "จำนวนเงิน"]
            )
        ],
        rows=[
            [
                {"column": f"c{i}", "value": value, "confidence": 0.95, "source_span": str(value)}
                for i, value in enumerate(["1", "5415280238060", "กระเป๋า", "1", "5490", "5490", "20", "4392"])
            ]
        ],
    )
    text = "1 5415280238060 กระเป๋า 1 5490 5490 20 4392"
    validator = ValidatorAgent(FieldCatalog(tmp_path))
    errors, _, _ = validator.validate("invoice", [], document_text=text, tables=[table])
    assert not any("line_items" in e for e in errors)
    table.rows[0][-1].value = "26450"
    errors, _, review = validator.validate("invoice", [], document_text=text, tables=[table])
    assert review and any("c7" in e for e in errors)


@pytest.mark.parametrize("value,span", [(6, "6.9"), ("00123", "123")])
def test_cells_do_not_accept_numeric_fragments_or_changed_ids(tmp_path, value, span):
    table = ExtractedTable(
        columns=[dict(key="code", label="Code")],
        rows=[[dict(column="code", value=value, confidence=0.9, source_span=span)]],
    )
    errors, _, review = ValidatorAgent(FieldCatalog(tmp_path)).validate(
        "invoice", [], document_text=span, tables=[table]
    )
    assert review and any("code" in e for e in errors)


def test_legacy_array_without_source_requires_review(tmp_path):
    field = ExtractedField(name="line_items", value='[{"quantity":1}]', confidence=0.99, source_span="1")
    errors, _, review = ValidatorAgent(FieldCatalog(tmp_path)).validate("invoice", [field], document_text=None)
    assert review and any("line_items" in e for e in errors)
