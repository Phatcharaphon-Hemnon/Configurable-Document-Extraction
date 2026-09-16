# Persistent completed-result cache (SQLite, no Redis)

## Design

- Store: `{PROJECT_CACHE_DIR}/result-cache.sqlite` (WAL, atomic transactions).
  Separate from the OCR cache (`ocr-results/`), which is unchanged.
- Scope: per-page completed results, including legitimate review outcomes
  (`needs_review=True` with `judge_status` passed/flagged/skipped).
- Never cached: errors, `failed_stage`, `judge_status="unavailable"`
  (cancellations, transient provider failures, incomplete processing). A
  skipped Judge is cacheable only via the strengthened skip gate.
- Partial submissions: each valid page caches independently; a partially
  failed upload never presents as complete.

## Fingerprint (no credentials)

`ResultCache.fingerprint_page()` hashes: file SHA-256 + filename + page
number + page-text SHA + OCR engine/languages/DPI/model-hashes/hybrid
fingerprint + provider endpoint + stage models + temperature/token caps +
router caps + strict-schema flag + few-shot count + judge-skip settings +
prompt/extractor/validator/judge/acceptance versions + catalog contents SHA.
API keys never enter keys or metadata. Any relevant change → new fingerprint
→ automatic invalidation. Catalog edits change the catalog SHA, so results
are never reused under a stale fingerprint.

## Storage

- TTL default 7 days (`RESULT_CACHE_TTL_SECONDS`), max 128 page entries
  (`RESULT_CACHE_MAX_ENTRIES`), deterministic eviction (oldest
  `computed_at` first).
- Payloads validated via `ExtractionResult.model_validate`; corrupt/expired
  entries are deleted and treated as misses.
- Stored payload strips per-submission bindings (source, blocks, full text,
  timings, usage); original timings + computation timestamp are kept in
  entry metadata for history.

## Reuse

Creates a normal new submission record: fresh job/page IDs, current source
references + filenames + previews. Prior job IDs and download/preview URLs
are never reused. `cache_metadata` carries fingerprint, original computation
time, original stage timings, lookup latency, and `hit_type="full"`.
Response `timings` report `result_cache_hits/misses/lookup_ms` (full =
all pages hit, partial = some, miss = none). Per-page `ocr_cached` still
distinguishes OCR-only hits.

## Flags

- `POST /extract?force_refresh=true` — bypass result cache (OCR cache stays
  on). UI: "Force refresh" button.
- `POST /extract?disable_caches=true` — bypass both caches (benchmark/debug).
- `RESULT_CACHE_ENABLED=false` — disable globally.
- Single-flight duplicate-running-request protection preserved
  (force-refresh probes bypass single-flight).

## Invalidation & clearing

`DELETE /api/history` clears jobs + sources + result-cache entries (409 while
active). Code/prompt/model/catalog changes invalidate by fingerprint without
manual clearing.

## Rollback

Set `RESULT_CACHE_ENABLED=false` (or revert `result_cache.py` + service
wiring + route params). Cached rows are inert without the lookup path; delete
`{cache}/result-cache.sqlite` to purge.
