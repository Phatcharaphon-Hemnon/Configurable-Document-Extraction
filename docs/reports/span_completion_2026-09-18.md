# Same-line span completion + Judge budget (acceptance v1.0.2) — 2026-09-18

## Why the SROIE receipt still showed many errors (after v1.0.1)

Running the real Tesseract path on `sroie_X51006857265.jpg` showed two
systematic causes on top of correct rejections:

1. **LLM under-quotes spans.** OCR text holds
   `INVOICE NO: 008558 | TRANS: 009628` verbatim, but the model quoted a
   label-only/partial span (`INVOICE NO:`) for value `008558` → `value not
   supported by source_span`. Same pattern for `invoice_date`, `tax_id`.
2. **Judge output truncated.** ~20 records → judge JSON hit the hardcoded
   `max_tokens=1000` (`finish_reason=length`, 4078-char fragment) →
   `Error serializing JudgeResponseSchema … Unterminated string` added as a
   validation error and the Judge went `unavailable`.

Genuinely destroyed evidence stays rejected (safe direction): OCR reads the
TOTAL line as `TOTAL | งไง` (the printed `31.45` is lost), so no
`total_amount` span can verify. Correct rejections also include
`bill_to_name="STICKERS & OTHER GIFT"` (an item description, not a buyer —
the guard caught a real misassignment), `currency=THB` (no mark printed),
and `payment_due_date` (no printed due date).

## Fix

- `api/app/services/acceptance.py::_complete_span_to_line` (**scalar fields
  only**): when a span IS verbatim page-local but does not support its
  value, and BOTH occur on ONE printed line, the span is repaired to that
  full line (stored span updated, `info` issue recorded). Same-line scoping
  is the safety argument: the Issue-4 scattered-words attack and wrong-row
  bindings span different lines and never repair. Numeric/date values match
  via the existing `value_in_text` (magnitudes, ISO normalizations).
  **Table cells are excluded**: a shared row-level span must never stand in
  for cell-level evidence (`test_row_span_cell_requires_cell_level_evidence`
  guards this — the first implementation repaired cells too and that test
  caught it).
- `api/app/agents/judge.py` + `api/app/core/config.py`: `max_tokens=1000`
  → `Settings.judge_max_tokens` (`JUDGE_MAX_TOKENS`, default 2500).
- `ACCEPTANCE_POLICY_VERSION` → `v1.0.2` (cache invalidation automatic).

## Deliberately NOT changed

No elsewhere fallback, no short-span overlap relaxation, no OCR-confusion
fuzzy match, no stripped-leading-zero acceptance. Total loss from OCR
(`TOTAL | งไง`) needs better OCR (RapidOCR/preprocessing), not weaker
verification.

## Verification

```bash
source .venv/bin/activate && ruff check api/
python -m pytest api/tests/test_acceptance.py api/tests/test_security.py \
  api/tests/test_review_contracts.py api/tests/test_evidence_precision.py \
  api/tests/test_guards.py api/tests/test_catalog_required_alignment.py \
  api/tests/test_pipeline.py api/tests/test_result_cache.py -q
```

New tests in `api/tests/test_acceptance.py` use real Tesseract line shapes
(`" | "` separators): `test_span_completed_to_printed_line`,
`test_span_repair_accepts_iso_date_on_same_line`,
`test_span_repair_rejects_cross_line_value` (Issue-4 intact),
`test_span_repair_rejects_value_missing_from_line`,
`test_cells_do_not_get_line_repair`.

## Operator note

Re-upload the receipt after deploy. Expected: `invoice_number`,
`invoice_date`, `tax_id` accept via line completion; `total_amount` stays
in review until OCR recovers the TOTAL line (evidence is genuinely absent).
