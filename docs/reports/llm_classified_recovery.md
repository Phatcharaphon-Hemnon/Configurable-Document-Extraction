# LLM classified recovery (THAI_bill.jpg table-as-root)

## Failure

`THAI_bill.jpg` (printed Thai receipt, Tesseract coherence 0.63, 200 blocks)
reached the extractor with `ollama-local / qwen2.5:3b`. The model returned a
single table object as the root:

```json
{"name":"line_items","columns":[{"key":"product_name","label":"..."}]}
```

Expected contract: one root `{fields:[...],tables:[...]}`. The old client
lumped every failure as “unparseable JSON” and blindly regenerated through
`json_schema → json_object → plain` (3 model calls, ~1000 s each on this CPU),
with logs claiming “attempt 1 returned invalid JSON” even when tier 1 was
skipped. The UI preview’s `[truncated, N chars]` marker (display-only) was
indistinguishable from model truncation.

## Fix (`app/services/client.py`)

- **Output contract** (`_build_json_prompt_suffix`): extraction schema now
  explicitly requires ONE root `{fields,tables}`, states a table object
  belongs inside `tables` (never as root), with a compact structural example
  (no reference answers; scalar vs cell kept distinct).
- **Classification** (`_classify_parse_failure`, `_try_parse_detailed`):
  `empty | syntax_error | wrong_root | table_root | field_error | truncated`
  with validation locations/types, raw length, and finish_reason. Only
  unambiguous wrappers (think blocks, one fence, one prose-wrapped object)
  are handled locally; ambiguous multiple objects and incomplete JSON are
  rejected without invention.
- **Table-root adapter** (`adapt_table_root`): requires an unambiguous,
  complete, valid `ExtractedTable`. Preserves contents, records normalization,
  never silently claims scalars completed (`fields=[]` is explicitly
  incomplete; coverage/needs_review flags it downstream).
- **Single corrective**: one initial generation in the strongest supported
  format, then at most ONE corrective in the same format (never an automatic
  plain downgrade for schema failures) with concise validation locations +
  required structure + original source, previous answer treated as untrusted.
  Confirmed `length` truncation raises honestly (no blind retry of the same
  oversized request). Transport retries share the same 4-attempt budget via
  `request_budget` (no extra layer). Unsupported-tier caching stays
  provider-rejection-only; malformed output never marks a tier unsupported.
- **Honest errors** (`_classified_error`): kind, locations, response length,
  tokens, elapsed, finish_reason, schema fingerprint, endpoint/model/format,
  and a display-only preview note. Finish_reason is extracted from the stored
  raw response; preview truncation is labeled display-only.

## Verification

- `test_classified_recovery.py` (11 tests): one-call success, table-root
  preservation + partial fallback, missing source_span single corrective,
  syntax vs schema kinds, display vs confirmed truncation, explicit rejection
  caching, disabled schema without false warnings, failed corrective
  diagnostics.
- Updated `test_client_fallback_tiers.py`, `test_client_retry.py`,
  `test_extraction_response_contract.py`, `test_request_queue.py` to the
  2-call classified budget (was blind 3-tier).
- Full backend suite: 395 passed; frontend build + 9 node tests pass.
- Live `qwen2.5:3b` rerun on `THAI_bill.jpg` was not attempted here (≈10–20
  min/page on this CPU, concurrency 1); parsing was verified with the exact
  screenshot shape synthetically. No accuracy gain is claimed until a warm-model
  run measures it.

## Limits

- Truncation recovery (compaction/split by row groups with coverage
  reconciliation) is diagnosed, not yet implemented — oversized requests fail
  honestly instead of burning retries.
- Field-level evidence failures still cost one corrective; full
  invalid-candidate preservation lives in `acceptance.py` (rejected/review),
  not in the client.
