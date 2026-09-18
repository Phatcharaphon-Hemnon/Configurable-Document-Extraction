# Extractor output truncation (finish_reason=length) — 2026-09-18

## Symptom
`Extractor failed — technical failure` on a `SHEET FOREST CAFE` tax invoice
(Table 15, 4 line-item rows) with provider `ollama-local`,
model `scb10x/llama3.2-typhoon2-3b-instruct`:

- `max_tokens=3000`, `finish_reason='length'`, `len=4792`
- `tokens=2158/3000/5158` (prompt / completion / total)
- `decode error: Unterminated string starting at pos 291`

`Retry (force refresh)` / `Retry uncached` could never succeed: the same
3000-token budget truncates the same way every time.

## Root cause
Scalar `fields[]` + `tables[line_items]` carry per-field/cell `confidence`
and verbatim `source_span` quotes. A 4-row × 5-column receipt plus ~10
scalars needs more than 3000 completion tokens on a small local model.
`api/app/services/client.py` correctly classifies `finish_reason='length'`
as `truncated` and fails fast with no blind retry
(`_generate_structured_inner`), so the partial JSON surfaces as a technical
failure. The deployed `api/.env` pinned `EXTRACTION_MAX_TOKENS=3000` while
the code default is `8000` (`api/app/core/config.py`).

## Fix (model unchanged)
1. `api/.env` + `api/.env.example`: `EXTRACTION_MAX_TOKENS=8000` with a
   comment documenting the 6000 table-safe floor and the Ollama
   `num_ctx` requirement (prompt + output must fit, e.g.
   `OLLAMA_NUM_CTX=12288`).
2. `api/app/services/client.py::_classified_error`: truncation errors now
   append a remediation hint (raise budget, check `num_ctx`, restart API,
   then Retry force-refresh) and carry structured `provider_details`
   (`error_type=LLMOutputTruncated`) so the UI `<details>` panel and logs
   show the actionable cause.
3. `api/app/agents/extractors.py::_run_call`: output-budget guard logs a
   warning when tables are enabled and `EXTRACTION_MAX_TOKENS < 6000`.

No prompt, schema, model, or retry-budget changes. Cache fingerprints
already include `extraction_max_tokens`, so old entries invalidate
automatically; no version bump needed (error path is never cached).

## Operator runbook
1. Set `EXTRACTION_MAX_TOKENS=8000` in `api/.env`, restart the API from
   `api/` so `.env` resolves.
2. Ensure Ollama context fits: `prompt (~2158) + 8000 < num_ctx`.
3. `Retry (force refresh)` on the failed job.
4. If truncation persists at 8000, the page needs split extraction
   (`REGION_EXTRACTION_ENABLED=true`) — not a larger single budget.

## Verification
- `ruff check api/` clean for touched files.
- `python -m pytest api/tests/test_classified_recovery.py api/tests/test_cache_generation_params.py -q`
- New assertion: truncated `ClientError` message contains `Remediation`
  and `EXTRACTION_MAX_TOKENS`; `provider_details.error_type ==
  LLMOutputTruncated`.
