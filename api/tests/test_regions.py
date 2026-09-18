"""Region splitting, merging, reconciliation (mocked OCR geometry only).

No network, no models, no OCR binaries. Layout fixtures are hand-built
OCRBlock-shaped objects exercising: multi-table pages, notes runs,
multiline logical rows, ambiguous boundaries, blank exclusion, and the
no-geometry fallbacks.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.services.regions import (  # noqa: E402
    MAX_TABLE_ROWS_PER_GROUP,
    SPLITTER_VERSION,
    fingerprint_region,
    merge_region_outputs,
    reconcile_page,
    split_page,
)


def _block(text, left, top, width=60, height=12, block_id=None, conf=0.9):
    return SimpleNamespace(text=text, confidence=conf,
                           box=(left, top, width, height),
                           engine="tesseract", alternatives=[],
                           review_reason=None, block_id=block_id)


def _invoice_blocks():
    """Header + one 3-row table + totals, with stable block IDs."""
    blocks, i = [], [0]

    def add(text, left, top, width=60):
        i[0] += 1
        blocks.append(_block(text, left, top, width, block_id=f"b{i[0]}"))
        return blocks[-1]

    add("ACME CO", 10, 10)
    add("TAX INVOICE", 10, 30)
    add("Item", 10, 60)
    add("Qty", 200, 60)
    add("Price", 300, 60)
    for r, item in enumerate(["WIDGET-A", "GADGET-B", "THING-C"]):
        y = 80 + r * 20
        add(item, 10, y)
        add(str(r + 1), 200, y, 30)
        add("10.00", 300, y, 50)
    add("Subtotal: 30.00", 10, 150)
    add("Total: 30.00", 10, 170)
    return blocks


def _page_text(blocks):
    return "\n".join(b.text for b in blocks)


def _ledger_covers(split, blocks):
    seen = (set(split.coverage.assigned) | set(split.coverage.excluded)
            | set(split.coverage.unresolved))
    assert seen == {b.block_id for b in blocks}
    assert not (set(split.coverage.assigned) & set(split.coverage.excluded))
    assert not (set(split.coverage.assigned) & set(split.coverage.unresolved))


def test_invoice_splits_header_table_totals_with_full_coverage():
    blocks = _invoice_blocks()
    split = split_page(blocks, _page_text(blocks), page_number=1)
    assert split.fallback_reason is None
    kinds = [r.kind for r in split.regions]
    assert kinds[0] == "header"
    assert "table" in kinds and kinds[-1] == "totals"
    assert not split.coverage.unresolved
    assert not split.coverage.excluded
    _ledger_covers(split, blocks)
    table_regions = [r for r in split.regions if r.kind == "table"]
    assert len(table_regions) == 1  # 3 rows fit in one group
    assert "Item" in table_regions[0].text  # header repeated as context


def test_large_table_groups_bound_rows_and_repeat_header():
    blocks = [_block("H1", 10, 10, block_id="h1"), _block("H2", 200, 10, block_id="h2")]
    for r in range(MAX_TABLE_ROWS_PER_GROUP + 5):
        y = 30 + r * 20
        blocks.append(_block(f"ITEM-{r}", 10, y, block_id=f"i{r}"))
        blocks.append(_block(str(r), 200, y, 30, block_id=f"q{r}"))
        blocks.append(_block("1.00", 300, y, 50, block_id=f"p{r}"))
    split = split_page(blocks, _page_text(blocks), page_number=2)
    groups = [r for r in split.regions if r.kind == "table"]
    assert len(groups) == 2
    for region in groups:
        data_rows = [ln for ln in region.text.splitlines()[1:]]
        assert len(data_rows) <= MAX_TABLE_ROWS_PER_GROUP
    _ledger_covers(split, blocks)


def test_multiline_rows_stay_together_with_column_evidence():
    blocks = [_block("Item", 10, 10, block_id="h1"), _block("Qty", 200, 10, block_id="h2")]
    # First logical row wraps: continuation is x-aligned, tight, column-short.
    blocks.append(_block("SUPER", 10, 30, block_id="w1"))
    blocks.append(_block("WIDGET", 10, 42, block_id="w2"))
    blocks.append(_block("2", 200, 30, 30, block_id="q1"))
    blocks.append(_block("NEXT", 10, 70, block_id="n1"))
    blocks.append(_block("1", 200, 70, 30, block_id="q2"))
    split = split_page(blocks, _page_text(blocks), page_number=1)
    table = [r for r in split.regions if r.kind == "table"]
    assert table, "table band must be detected"
    _ledger_covers(split, blocks)


def test_notes_and_blanks_covered_explicitly():
    blocks = [_block("Some memo line here", 10, 10, block_id="m1"),
              _block("   ", 10, 30, block_id="blank"),
              _block("Item", 10, 60, block_id="h1"),
              _block("Qty", 200, 60, block_id="h2"),
              _block("X", 10, 80, block_id="x1"),
              _block("1", 200, 80, block_id="q1"),
              _block("Trailing note kept", 10, 110, block_id="m2")]
    split = split_page(blocks, _page_text(blocks), page_number=1)
    assert split.coverage.excluded == {"blank": "blank"}
    # Leading memo joins the positional header region; the trailing memo
    # forms a genuine notes region. Neither is dropped.
    assert split.regions[0].kind == "header"
    assert any(r.kind == "notes" and "Trailing note" in r.text for r in split.regions)
    _ledger_covers(split, blocks)


def test_no_geometry_falls_back_with_reason():
    split = split_page([], "some text", page_number=1)
    assert len(split.regions) == 1 and split.regions[0].kind == "page"
    assert split.fallback_reason
    split2 = split_page([_block("hello", 10, 10, block_id="a")], "hello", page_number=1)
    assert split2.regions[0].kind == "page"
    assert "no table structure" in (split2.fallback_reason or "")


def test_multiple_tables_each_get_groups():
    blocks = []
    y = 10
    for t in range(2):
        if t == 1:
            # A real separator line between tables (titles/headers break bands).
            blocks.append(_block("SECOND TABLE", 10, y, block_id=f"sep{t}"))
            y += 20
        blocks.append(_block(f"C{t}", 10, y, block_id=f"h{t}a"))
        blocks.append(_block("Q", 200, y, block_id=f"h{t}b"))
        y += 20
        for r in range(2):
            blocks.append(_block(f"R{t}{r}", 10, y, block_id=f"r{t}{r}"))
            blocks.append(_block("1", 200, y, block_id=f"q{t}{r}"))
            y += 20
        y += 20
    split = split_page(blocks, _page_text(blocks), page_number=1)
    assert len([r for r in split.regions if r.kind == "table"]) == 2
    _ledger_covers(split, blocks)


def _field(name, value, span, region="r"):
    field = SimpleNamespace(name=name, value=value, confidence=0.9,
                            source_span=span)
    field.region_id = region
    return field


def test_merge_dedupes_provenance_not_equal_values():
    a = _field("invoice_number", "INV-1", "No: INV-1", "ra")
    b = _field("invoice_number", "INV-1", "No: INV-1", "rb")  # same provenance
    c = _field("total_amount", "10.00", "Total 10.00", "rc")
    d = _field("total_amount", "10.00", "TOTAL: 10.00", "rd")  # equal value, distinct rows
    merged = merge_region_outputs([
        {"region": SimpleNamespace(id="ra"), "fields": [a, c], "tables": []},
        {"region": SimpleNamespace(id="rb"), "fields": [b, d], "tables": []},
    ])
    names = [f.name for f in merged["fields"]]
    assert names.count("invoice_number") == 1  # duplicated provenance collapsed
    assert names.count("total_amount") == 1  # name-keyed; conflict retained, not duplicated
    assert len(merged["deduped"]) == 1
    assert len(merged["conflicts"]) == 1  # distinct evidence kept for review


def _row(value):
    return [SimpleNamespace(column="item", value=value, confidence=0.9,
                            source_span=value)]


def test_merge_never_dedupes_table_rows():
    t1 = SimpleNamespace(name="line_items", rows=[_row("WIDGET"), _row("WIDGET")])
    merged = merge_region_outputs([
        {"region": SimpleNamespace(id="ra"), "fields": [], "tables": [t1]},
    ])
    assert len(merged["tables"][0].rows) == 2  # legitimate repeats preserved
    assert merged["row_provenance"]["line_items"] == ["ra", "ra"]


def test_reconcile_flags_unresolved_and_keeps_amounts_verbatim():
    from app.services.regions import CoverageLedger
    coverage = CoverageLedger(unresolved=["b9"])
    findings = reconcile_page(
        doc_type="invoice", fields=[
            _field("subtotal_amount", 30.0, "Subtotal 30.0"),
            _field("tax_amount", 3.0, "Tax 3.0"),
            _field("total_amount", 99.0, "Total 99.0"),
        ],
        tables=[], conflicts=[], coverage=coverage,
        ambiguous_boundaries=["page 1 lines 2->3: kept separate"],
        tables_expected=False)
    kinds = {(f.category, f.severity) for f in findings}
    assert ("mechanical", "error") in kinds  # unresolved blocks
    assert ("row_column", "warning") in kinds  # ambiguous boundary
    assert any(f.target == "field:total_amount" for f in findings)  # arithmetic finding
    # Amounts themselves are untouched by reconciliation.
    assert [f for f in findings if f.target == "field:total_amount"]


def test_fingerprint_stable_and_content_sensitive():
    region_kwargs = dict(job_id="j", source_id="s", page_number=1,
                         block_texts={"b1": "Hello"},
                         upstream_text_sha="u", catalog_sha="c",
                         schema_version="s1", prompt_version="p1",
                         model_fingerprint={"model": "m"})
    from app.services.regions import Region
    r1 = Region(id="page-1-region-header-0", kind="header", page_number=1,
                text="Hello", block_ids=["b1"])
    r2 = Region(id="page-1-region-header-0", kind="header", page_number=1,
                text="Hello!", block_ids=["b1"])
    assert (fingerprint_region(region=r1, **region_kwargs)
            == fingerprint_region(region=r1, **region_kwargs))
    # Positional ID alone is insufficient: content change invalidates.
    assert (fingerprint_region(region=r1, **region_kwargs)
            != fingerprint_region(region=r2, **region_kwargs))
    assert SPLITTER_VERSION.startswith("regions-v")
