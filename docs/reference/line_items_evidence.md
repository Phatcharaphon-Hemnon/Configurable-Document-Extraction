# `line_items` Evidence Check (Per-Cell)

> Last updated: 2026-09-07. Replaces the whole-row concatenation check that
> false-positived on photo OCR (GL Handicraft receipt: real row 0 flagged).

## Why per-cell

The old check (`validator.py::_check_array_rows`) joined a row's values —
`"SAFETY PINS BUTTERFLY - S 6.0 17.0 102.0"` — and required that string to
appear contiguously in the OCR text (else ≥ 0.75 token overlap). Two normal
things broke it:

1. **Photo OCR splits rows across lines**: `… BUTTERFLY - S` /
   `6.00 BOXS x 17.00 102.00 SR` — the concatenation never occurs verbatim
   because `BOXS x` / `SR` sit between the cells.
2. **Number serialization**: the extractor coerces JSON numbers to float, so
   `json.dumps` yields `"6.0"` while OCR reads `"6.00"`. Three such cells
   dragged overlap to 0.625 < 0.75 → `array row 0 not backed by document
   evidence (possible hallucination)` on a genuine row.

## Current logic (`api/app/agents/validator.py`)

Each dict row is verified **cell by cell**; the row fails on the first
unevidenced cell. The error names the cell for debuggability but keeps the
legacy substring so existing monitors still match:

```
line_items: array row 0 not backed by document evidence
  (possible hallucination: cell 'quantity'='9')
```

- **Text cells**: contiguous normalized substring wins; else short cells
  (≤ 3 tokens, e.g. `Item A`, `SR`) require every token present; longer
  descriptions allow ≥ 0.75 token overlap (OCR-noise tolerance).
- **Numeric cells** (`_parse_number`, strips `RM/$/%`/thousands): compare by
  **magnitude** against numbers found in the OCR text (half-cent tolerance),
  so `6` == `6.0` == `6.00`. Substring matching is deliberately *not* used
  for numbers — otherwise quantity `9` would falsely match the `9` inside
  OCR `19`, and `6` would match `96.23`.
- **Computed extensions** (`_amount_consistent`): an amount-like cell
  (`amount`/`total*`/`extended*`, excluding `unit_*`) is accepted when it
  equals `quantity × unit_price` (±1 %) from evidenced inputs — e.g. `60`
  from `Qty 2 Price 30` passes even though `60` never appears in the text.
  Only the amount cell is rescued; phantom descriptions/quantities/prices in
  the same row are still flagged.
- Non-dict (plain-string) rows keep the old whole-row match as fallback.
- Empty/None document text returns no row errors (the scalar
  `check_evidence` flags that case instead). All bad rows are reported, not
  just the first.

## Known tradeoff

A fully fabricated row whose amount happens to equal qty × unit *and* whose
other cells all appear elsewhere in the document could pass. Accepted: the
blob-level `source_span` check plus the Judge stage still cover that case,
and the alternative (flagging computed extensions) false-positives on real
receipts.

## Related changes (same fix batch)

- `api/app/agents/extractors.py`: row key instruction aligned with the
  catalog (`description, quantity, unit_price, amount`) + "omit unreadable
  rows, never invent".
- `api/app/services/rapidocr_client.py`: JPG autocontrast + upscale (short
  side → ~1500px, ≤ 3x) and row tolerance adaptive to median box height
  (12–40px) instead of fixed 12px.
- Tests: `api/tests/test_validator_array_rows.py` (receipt repro + phantom
  + numeric-format + fallback cases).
