# Changelog

All notable changes to this project, newest first. Deep dives live in `docs/`.

## 2026-09-10 — Zero-review loop: evidence-guard precision + eval re-runs

**Problem:** 2026-09-09 eval at 100% `needs_review` (294 flags, 14/14 pages).
Triage (`docs/review_triage.md`) showed mostly validator false positives:
quoted/bracketed spans (139), OCR-spacing/date-format mismatches, plus
`required` flags for fields no gold PO/DN prints.

**Changed** (strict-direction only — flag, never drop):
- `api/app/core/security.py` — quote/bracket/escape-tolerant span matching,
  OCR-spacing + comma-decimal + dot-drop numerics, date-aware values,
  short-span verbatim rule, empty-text flags, image-bypass removed.
- `api/app/agents/validator.py` — doc-grounded fallback for imprecise
  row-level table spans (phantoms still flag); empty-text arrays flag.
- `api/app/schemas/llm_schemas.py` — `source_span` required non-empty
  (span-less output retries). `extraction_service.py` — verbatim-numeric
  judge gate; info-only judge issues don't force review. `judge.py` —
  provenance block in prompt. `field_catalog.py` — new-field floor
  `max(configured, 0.8)`; Thai no-data placeholders omitted.
- `extractors.py` prompt — bare verbatim spans, placeholder ban. Catalogs —
  PO totals/currency/supplier + DN delivery_date optional (gold-backed).
- New scripts: `audit_review_causes.py` (triage ledger),
  `merge_eval_runs.py` (chunked-run merge). New tests:
  `test_evidence_precision.py`, `test_catalog_required_alignment.py`.

**Verified:** 337 passed, 2 skipped, ruff clean. Live `qwen2.5:3b` loop
(2 chunks merged): review 100% → 64% (5/14 clean, 4 judge-skipped),
macro F1 0.439 → 0.446, router 1.000. Remainder = genuine small-model
errors for the stronger-model loop. See `docs/review_zero_plan.md`.

## 2026-09-04 — Langfuse v4 tracing overhaul

**Problem:** tracing was silently dead — the wrapper called
`client.trace()`, which doesn't exist in SDK v4, so with real keys
configured zero traces were sent (failures swallowed by try/except).

**Changed** (per vendored `skills/langfuse`, audited live vs
https://langfuse.com/docs/observability/best-practices):
- Rewrote `api/app/observability/langfuse.py` on the v4 API: one
  `extract-document` trace per page with nested children —
  `classify-document` / `extract-fields` / `judge-extraction` as
  generations (real model + token usage → cost tracking),
  `ocr-page` / `validate-fields` / `catalog-update` as spans; trace I/O
  and `judge-score` / `completeness` / `needs_review` scores.
- Chose manual observations over the OpenAI integration (no PII masking
  hook there, generation-per-retry noise, third-party gateway risk).
- Token usage plumbed via `SutGenAIClient.last_usage` (no signature
  changes; mock-safe). PII mask hook over the project's `PIIDetector`.
- Fixed two live-found bugs: explicit `trace_context` orphans observations
  into separate traces (use root-handle nesting); un-ended spans are never
  exported (all return paths end the root).
- `api/requirements.txt`: `langfuse>=4.0`. `api/tests/test_langfuse_tracing.py`:
  7 new tests. See `docs/langfuse_tracing.md`.

**Verified:** live extraction → fetched trace back from Langfuse → single
nested tree with models, usage, scores, I/O all present.

## 2026-09-04 — Async extraction jobs (fix socket hang-ups)

**Problem:** uploads held one HTTP connection open for the whole minute-long
pipeline. Any refresh, retry click, or proxy drop killed the extraction
mid-flight (`vite proxy error: socket hang up`, empty-body `JSON.parse`
crash), orphaned `queued` rows, and stacked duplicate pipelines on retry.

**Changed** — `POST /api/extract` returns **202 + `job_id` in ~0.1s**;
extraction runs as a background task; the client polls `GET /jobs/{job_id}`:
- `api/app/api/routes.py` — async endpoint, single-flight registry (identical
  bytes re-uploaded while running reuse the same job), rate-limit slot held
  until the background task finishes.
- `api/app/services/extraction_service.py` — `extract_group(parts, job_id=...)`
  reuses the caller's row; `CancelledError` marks jobs `failed` instead of
  orphaning them; new `run_job()` background entry point; `get_batch_status()`
  surfaces `error` for failed jobs; boot-cleanup marks stale `queued` rows
  `failed` on startup.
- `api/app/services/job_store.py` — `fail_job()`, `get_error()`,
  `fail_stale_queued()` on both stores; fixed SQLite `get()` reconstruction
  (JSON-string columns deserialized, required `request` block rebuilt — polls
  previously validated to `None`).
- `api/app/database/job_repository.py` — `fail_stale_queued_jobs()`.
- `api/app/schemas/documents.py` — `BatchStatusResponse.error`.
- `api/tests/test_async_jobs.py` — 6 new tests (job reuse, cancel marking,
  error surfacing, stale cleanup).
- `web/src/api/client.ts` — `extractFiles()` returns `{job_id}`;
  `pollJobStatus()` (2s interval, 10-min cap); `requestJson` reads text
  first (no more cryptic `JSON.parse` banner on empty bodies).
- `web/src/hooks/useDocumentQueue.ts` — poll flow, one run per group,
  Retry gated while `uploading`.
- `web/src/types/extraction.ts` — `JobAcceptedResponse`, `JobStatusResponse`.
- `web/src/components/HistoryTab.tsx` — 15s fetch timeouts → error UI.
- `docs/async_jobs.md` — full design + verification notes.

**Verified:** 148 backend tests pass, frontend build clean, live: 202 in
0.1s → poll completes with full documents; re-upload returns same id.
Requires restarting the backend (running server still has sync code).

## 2026-09-04 — LLM timeout recovery

**Problem:** strict-schema calls hung 90s with empty responses
(Router 90s+ → Extractor 109s → ~6 min/document).

**Changed** (diagnosed first with `api/scripts/time_gateway_modes.py`, which
proved strict mode healthy ~3–5s and fallbacks unparseable — so strict stays
ON): same-tier timeout retry in `sut_genai_client.py`; `LLM_REQUEST_TIMEOUT_SECONDS`
90→45, `EXTRACTION_MAX_TOKENS` 8000→3000; wired dead `JUDGE_SKIP_WHEN_CLEAN`
config; `TimeoutGuard.track()` now really enforces (was warn-only) with limits
router 100 / extractor 150 / judge 100; Python floor 3.10→3.11
(`scripts/run_all.sh`, `README.md`). See `docs/timeout_recovery.md`.

## 2026-09-04 — Catalog review helper + discovery fix

**Changed:** new `api/scripts/review_discovered_fields.py` (lists
`ai_discovered` fields with `LONG>30`/`DIGITS`/`GENERIC`/`NEAR-DUP` flags) +
3 tests; strengthened the extractor prompt's new-field rule after a live test
proved the LLM silently dropped a clearly labeled value (`Loyalty Earned`
now registers as `loyalty_earned`). See `docs/catalog_review.md`.

## 2026-09-04 — LlamaParse fully removed

Deleted `api/app/services/llamaparse_client.py` and every reference
(config, `.env.example`, CI env, `run_all.sh` warning, skill doc); rewrote
`docs/paddleocr_migration.md` as `docs/local_ocr.md` (current-state doc).

## 2026-09-04 — Local OCR on RapidOCR (Python 3.14)

Replaced cloud OCR with on-host `rapidocr_onnxruntime==1.2.3`
(`api/app/services/rapidocr_client.py`; PDFs via PyMuPDF at 300 DPI; single
text model, no vision model). PaddleOCR was tried first but cannot execute
here — `paddlepaddle` publishes no Python 3.14 wheels and only 3.14 exists
on this host. Verified live: synthetic invoice → correct OCR in ~1.5s CPU.

## 2026-09-04 — Clean reinstall

Wiped `.venv` and reinstalled from corrected requirements: 5.7G → 643M,
`llama-cloud-services`/`paddleocr`/`paddlex` gone.

## 2026-09-03 — Guardrails, hallucination fix, SQLite + History

AI guardrails module (`api/app/guards/`: input, rate-limit, content,
timeout, audit, PII, output) wired into routes + service; `extraction_source`
(`vision`/`ocr`/`text`) with relaxed evidence check for image extractions;
SQLite persistence (`api/app/database/`, `SQLiteJobStore`) with `extract_group`
saving every result; `GET /history` + stats + frontend History tab;
`FieldCatalog` mtime caching; `job_id` on `FileExtractionResponse`.
