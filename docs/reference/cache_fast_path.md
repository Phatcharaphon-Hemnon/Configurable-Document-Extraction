# Completed-result cache fast path

Repeated uploads must not wait behind slow jobs or redo OCR/LLM work. The
per-page result cache already skipped LLM rework, but only *after* the
processing-job lock and *after* full OCR (the page fingerprint needs OCR
text). The fast path resolves repeated files *before* the lock and OCR.

## How it works

`run_job` tries `_try_fast_path(job_id, parts)` first; a FULL hit returns
the completed response immediately. Anything else (miss, partial, corrupt,
expired, unrenderable, `force_refresh`/`disable_caches`) falls through to
the normal locked path unchanged.

1. **Manifest lookup** — `ResultCache.manifest_key(file bytes, filename,
   page count)` embeds the *entire* configuration fingerprint (OCR
   engine/langs/DPI/model policy, provider endpoint + stage models,
   generation settings, prompt/client/extractor/validator/judge/acceptance
   versions, catalog hash). No invalidation was weakened: any relevant
   change is a miss. `manifest_get` returns the ordered full page
   fingerprints plus stored page texts.
2. **Entry resolution** — every fingerprint is fetched and contract-validated
   (`get` removes corrupt/expired entries). One miss → fall through; a
   partial result is never presented as complete.
3. **Normal new submission** — the job row is marked processing (so
   History-clear refuses mid-serve), sources are saved (fresh IDs), page
   previews are re-rendered without OCR, and fresh preview/download URLs are
   issued. Cached payloads never carry old IDs or URLs.
4. **Provenance** — original `extracted_at` is preserved; original stage
   timings travel in `cache_metadata.original_timings`; current
   lookup/render latency is reported separately in response `timings`
   (`fast_path=1.0`, `queue=0.0`, `result_cache_lookup_ms`,
   `result_cache_render_ms`).

## Safety properties

- **No shared mutable extraction state**: fast path touches only source
  storage (fresh UUID dirs), render-only page images, SQLite reads, and its
  own job row — safe to run while another job holds `_job_lock`.
- **Duplicate protection**: the routes single-flight map is unchanged;
  concurrent identical uploads each complete independently with distinct
  source IDs (covered by test).
- **History clearing**: `clear_history` refuses while jobs are active, and
  `ResultCache.clear()` wipes entries *and* manifests together, so cleared
  results cannot resurrect through the fast path (covered by test).
- **Cancellation**: `CancelledError` propagates to the standard
  persist-and-raise path.
- **Test doubles**: services built via `__new__` without a `result_cache`
  attribute skip the fast path (attribute-tolerant guard), preserving the
  FIFO-queue tests.
- **No credentials** in fingerprints, manifests, logs, or reports (endpoint
  URL + model names only, as before).

## Explicit document type

`POST /extract` accepts `doc_type=invoice|purchase_order|delivery_note`.
A valid selection bypasses Router classification (one fewer LLM call) via a
synthetic `RoutingDecision(confidence=1.0)`; extraction validation,
evidence checks, and Judge policy run unchanged. Unknown values are HTTP
400 (`parse_doc_type`, shared with the service layer). Omit the parameter
for automatic classification. The selection joins the single-flight
fingerprint, since it changes routing.

## Cloud output formats (verified, no code change)

Official Ollama docs state cloud structured outputs are *not* enforced
(`response_format: json_schema` is silently ignored, never 400-rejected).
The client was already correct for this: the JSON-schema prompt suffix is
always sent, malformed output triggers at most ONE same-tier corrective
generation, tiers are remembered as unsupported only on explicit
parameter rejection, and Pydantic + evidence validation run regardless of
transport. If cloud enforcement ever lands, the `json_schema` tier starts
working with no code change. Covered by
`test_malformed_output_keeps_strongest_tier`.

## Measurements (honest scope)

- Full-hit latency is provider-independent (no LLM): measured locally, see
  the benchmark report. Uncached cloud timings need cloud credentials and
  are reported as blocked, not mocked.
- Key timings to compare per run: `processing`, `queue`,
  `result_cache_lookup_ms`, `result_cache_render_ms`, plus per-stage
  `ocr`/`render`/`router`/`extractor`/`judge` in document timings and the
  shared 4-attempt HTTP budget accounting in client logs.

## Sync note (local branch)

This is shared application logic. `chore/eval-3b-combined-report` needs the
identical commits (fast path + `doc_type` + this doc) to keep branch
parity; the deployment allowlist (`api/.env.example`, `README.md`) is
untouched by this change.
