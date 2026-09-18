"""Table column-key mismatch regression: rows must follow declared columns.

Failure mode: the extractor LLM declares columns [description, qty, total]
but sends row cells with synonym keys [item, quantity, amount] → every row
structurally invalid → whole table withheld. The prompt fix (EXACT SAME key
strings) is primary; the acceptance layer adds a zero-overlap positional
recovery so wholesale synonym schemas still yield reviewable rows.
"""

from __future__ import annotations

import sys
from pathlib import Path

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.agents.extractors import _COMMON_RULES  # noqa: E402
from app.schemas.documents import ExtractedTable  # noqa: E402
from app.services.acceptance import accept_page  # noqa: E402

PAGE = "Nasi Goreng | 2 | 25.00\nTotal RM 27.00"


def _table(columns, row_cells):
    return ExtractedTable(
        name="line_items",
        columns=[{"key": k, "label": k} for k in columns],
        rows=[[dict(cell) for cell in row_cells]],
    )


def _cell(column, value, span=None):
    return {"column": column, "value": value, "confidence": 0.9,
            "source_span": span or value}


def test_prompt_fix_column_key_consistency():
    assert "EXACT SAME key strings" in _COMMON_RULES
    assert "'item', 'quantity', 'amount'" in _COMMON_RULES
    assert "Name the item table 'line_items'" in _COMMON_RULES


def test_prompt_currency_never_defaults_thb():
    assert "Never default to THB" in _COMMON_RULES
    assert "RM/Ringgit" in _COMMON_RULES


def test_row_with_positional_alignment_when_counts_match():
    table = _table(
        ["description", "qty", "total"],
        [_cell("item", "Nasi Goreng"), _cell("quantity", "2"), _cell("amount", "25.00")],
    )
    accepted, tables, rejected, issues, _ = accept_page(
        "invoice", page_number=1, fields=[], tables=[table],
        page_text=PAGE, blocks=[], catalog=None,
    )
    assert len(tables) == 1
    assert [c.column for c in tables[0].rows[0]] == ["description", "qty", "total"]
    assert [c.value for c in tables[0].rows[0]] == ["Nasi Goreng", "2", "25.00"]
    assert any("aligned positionally" in i.explanation for i in issues)
    assert not [r for r in rejected if r.kind == "row"]
    assert accepted == []


def test_row_with_synonym_keys_rejected_without_repair():
    # Same synonym problem but cell COUNT differs → no positional recovery.
    table = _table(
        ["description", "qty", "total"],
        [_cell("item", "Nasi Goreng"), _cell("quantity", "2")],
    )
    _, tables, rejected, _, _ = accept_page(
        "invoice", page_number=1, fields=[], tables=[table],
        page_text=PAGE, blocks=[], catalog=None,
    )
    assert tables == []
    assert any(r.kind in ("row", "table") for r in rejected)


def test_partial_overlap_stays_structural_error():
    # The model attempted the declared schema ([a] matches) but got [b]
    # wrong → must fail loudly, never be guessed into shape.
    table = _table(
        ["a", "b"],
        [_cell("a", "Item A", span="Item A 10"), _cell("c_unknown", "10", span="Item A 10")],
    )
    _, tables, rejected, _, _ = accept_page(
        "invoice", page_number=1, fields=[], tables=[table],
        page_text="Item A 10\nTotal 100", blocks=[], catalog=None,
    )
    assert tables == []
    assert rejected


RECEIPT_PAGE = (
    "Nasi Goreng | 1 | 12.50 | 12.50 | 0.75\n"
    "Teh Tarik | 2 | 3.00 | 6.00 | 0.36\n"
    "Total RM 19.61"
)


def test_partial_overlap_majority_mismatch_realigns_positionally():
    # Receipt shape: 1 of 5 keys matches (description) → synonym drift →
    # positional realign with a partial-overlap finding.
    table = _table(
        ["description", "qty", "unit_price", "total", "tax"],
        [
            _cell("description", "Nasi Goreng"),
            _cell("quantity", "1"),
            _cell("price", "12.50"),
            _cell("amount", "12.50"),
            _cell("sr", "0.75"),
        ],
    )
    _, tables, rejected, issues, _ = accept_page(
        "invoice", page_number=1, fields=[], tables=[table],
        page_text=RECEIPT_PAGE, blocks=[], catalog=None,
    )
    assert len(tables) == 1
    assert [c.column for c in tables[0].rows[0]] == [
        "description", "qty", "unit_price", "total", "tax"]
    assert [c.value for c in tables[0].rows[0]] == [
        "Nasi Goreng", "1", "12.50", "12.50", "0.75"]
    assert any("partial-overlap, 1 of 5 keys matched" in i.explanation for i in issues)
    assert not [r for r in rejected if r.kind == "row"]


def test_partial_overlap_majority_match_stays_rejected():
    # 3 of 4 keys match → genuine partial data, not synonym drift.
    table = _table(
        ["a", "b", "c", "d"],
        [
            _cell("a", "Item A", span="Item A 10 20 30"),
            _cell("b", "10", span="Item A 10 20 30"),
            _cell("c", "20", span="Item A 10 20 30"),
            _cell("x_unknown", "30", span="Item A 10 20 30"),
        ],
    )
    _, tables, rejected, _, _ = accept_page(
        "invoice", page_number=1, fields=[], tables=[table],
        page_text="Item A 10 20 30", blocks=[], catalog=None,
    )
    assert tables == []
    assert rejected


def test_count_mismatch_always_rejected():
    table = _table(
        ["a", "b", "c"],
        [_cell("x", "1", span="1 2 3 4"), _cell("y", "2", span="1 2 3 4"),
         _cell("z", "3", span="1 2 3 4"), _cell("w", "4", span="1 2 3 4")],
    )
    _, tables, rejected, _, _ = accept_page(
        "invoice", page_number=1, fields=[], tables=[table],
        page_text="1 2 3 4", blocks=[], catalog=None,
    )
    assert tables == []
    assert rejected


def test_structural_reject_reports_actual_keys():
    # Regression diagnostics: the reason must name both key sets so the
    # next mismatch is debuggable without re-running the LLM.
    table = _table(
        ["a", "b"],
        [_cell("a", "Item A", span="Item A 10"), _cell("c_unknown", "10", span="Item A 10")],
    )
    _, tables, rejected, _, _ = accept_page(
        "invoice", page_number=1, fields=[], tables=[table],
        page_text="Item A 10\nTotal 100", blocks=[], catalog=None,
    )
    assert tables == []
    row_reject = next(r for r in rejected if r.kind == "row")
    assert "['a', 'c_unknown']" in row_reject.rejection_reason
    assert "['a', 'b']" in row_reject.rejection_reason
    assert any("row_keys=" in f for f in row_reject.validation_findings)
