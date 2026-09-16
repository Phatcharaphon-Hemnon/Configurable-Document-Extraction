# Cloud latency: completed-result fast path + explicit type (2026-09-14)

Branch: `main` (cloud/API-key deployment). Hardware: i3-1215U (8 CPU), 7 GB
RAM. Effective provider/model for live calls: cloud `ollama-cloud` /
`gpt-oss:20b`, concurrency 1 — **no cloud credentials were available in this
runtime, so no live cloud LLM call was made and no cloud timing is claimed.**

## What changed (shared application logic, main only)

- `api/app/services/result_cache.py` — `result_manifest` table + config-only
  fingerprint shared by page fingerprints and manifest keys; entries and
  manifests cleared together; no credentials in keys.
- `api/app/services/extraction_service.py` — `_try_fast_path` before the
  processing-job lock (full hits skip lock/OCR/LLM; partial/corrupt/miss
  falls through); manifest recording on cache put; optional `doc_type`
  bypassing Router classification only.
- `api/app/api/routes.py` — `POST /extract` accepts
  `doc_type=invoice|purchase_order|delivery_note` (400 otherwise, joins the
  single-flight key).
- `docs/reference/cache_fast_path.md` — design, safety, sync note.
- Tests: `api/tests/test_cache_fast_path.py` (17 tests).

## Measured (monotonic clocks, isolated storage)

Reference: `Invoice1.jpg` (373 KB, 1 page). OCR engine Tesseract
(`eng+tha`), real binary + models; LLM stages mocked (their latency is
*not* measured and *not* claimed).

| Run | Result | Notes |
|---|---|---|
| Warm uncached, real OCR + mocked LLM | **6.35 s** (OCR 4.42 s, render 0.53 s) | Local portion of a first-time run; cloud LLM time unmeasured |
| Repeat completed (fast path) | **0.72 s** (lookup 1.2 ms, render 0.71 s) | No lock, no OCR, no LLM; `queue=0.0`, `fast_path=1.0` |
| Repeat completed again | **0.69 s** | Stable, well under the 5 s repeat target |

Upload-to-first-usable-page == upload-to-final-result on fast-path hits
(single completed save). Queue time 0.0 by construction (lock bypassed);
a dedicated test holds the lock with a slow job and asserts the cached
repeat still completes with zero LLM/OCR calls.

## Accuracy preserved

Fast-path documents are content-identical to fresh runs modulo
per-submission metadata (test asserts field/table/evidence/validation
equality, original `extracted_at` + original timings preserved,
`hit_type="full"`). Pydantic + evidence validation untouched; explicit
`doc_type` still runs full extraction validation; Judge-skip criteria
unchanged. Partial hits serve cached pages and queue only missing pages
(test asserts exactly one extractor call for a 1-cached/1-missing pair).

## Verified without code change (§4)

Official Ollama docs: cloud structured outputs are *not* enforced
(`response_format: json_schema` silently ignored, never 400-rejected).
The client already handles this correctly (prompt-embedded schema always
sent; at most one same-tier corrective generation; tiers remembered only
on explicit rejection). New test pins malformed-output-keeps-tier.

## Status per target

- Repeat completed < 5 s: **met** for the app-controlled path (0.7 s
  measured; provider-independent).
- New 1–3-page < 60 s: **unmeasured/blocked** — needs cloud credentials for
  the Router/Extractor/Judge stages (local OCR alone is ~5–6 s/page on this
  host, leaving ample budget, but no claim is made without a live run).
- Accuracy/evidence: **met** (431-test suite green: see below).

## Tests executed

- `ruff check api/` clean; `pytest api/tests/`: **431 passed
  (414 existing incl. 17 new), 2 skipped**; `npm test` 9/9; `npm run build`
  ok. One regression caught and fixed during development (fast-path guard
  vs `__new__`-built test doubles; one change-detector test updated for the
  fingerprint refactor — coverage preserved functionally).
- Blocked: live cloud benchmark (no `LLM_API_KEY`/`OLLAMA_API_KEY`),
  browser upload flow against a live backend.

## Remaining bottlenecks / follow-ups

- First-time runs are OCR-bound locally (4.4 s/page Tesseract here);
  provider stages dominate on cloud — measure live when keyed.
- Fast-path render (~0.7 s/page at 300 DPI) is the repeat-path floor;
  lower preview DPI is possible but changes preview bytes vs fresh runs.
- Compact source-ID output (§5) evaluated and deferred: needs contract
  changes across extractor/validator/acceptance; not required for targets.
- OCR model prewarm at startup not added (first-upload cost); services are
  process singletons so models load once.

## Rollback / config

Revert the fast-path + `doc_type` commits on `main` (shared-logic files
only; deployment allowlist untouched). No new settings; to disable at
runtime set `RESULT_CACHE_ENABLED=false` (fast path and manifests go quiet;
normal path unchanged). `force_refresh=true` / `disable_caches=true`
semantics preserved. Sync note: `chore/eval-3b-combined-report` needs the
identical commits for parity — not applied automatically.
