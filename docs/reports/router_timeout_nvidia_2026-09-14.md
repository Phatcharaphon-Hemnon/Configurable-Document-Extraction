# Router timeouts + NVIDIA model evaluation (2026-09-14)

Branch: `main`. Provider: NVIDIA (`https://integrate.api.nvidia.com/v1`,
concurrency 1). OCR: Tesseract `eng+tha`, 300 DPI (unchanged — OCR already
fast at ~1s/page).

## Findings (stored result)

| Stage | Duration |
|---|---|
| OCR | 0.97s |
| Router | 200s, then cancelled |
| Extraction / validation / judge | Never started |

Root cause: runtime `api/.env` set `LLM_REQUEST_TIMEOUT_SECONDS=1000` with
`ROUTER_TIMEOUT_SECONDS=200`. The stage cancels the request before its
timeout retry can run (no provider details saved, so provider latency was
unconfirmed). The “INVOICE” badge was a failure placeholder (no routing ran).
History retry buttons only navigated back; they did not retry extraction.

## Configuration (implemented)

- `LLM_REQUEST_TIMEOUT_SECONDS=45`, `ROUTER_TIMEOUT_SECONDS=100`,
  `JUDGE_TIMEOUT_SECONDS=100`, `EXTRACTOR_TIMEOUT_SECONDS=150`
  (`api/.env.example` + `Settings` default 45s; runtime `api/.env` fixed from
  1000/200/700/2100). One 45s request + 2s backoff + one 45s retry ≈ 92s fits
  inside 100s Router/Judge; Extractor 150s allows the same retry plus the
  single corrective generation. Shorter limits bound failures; they do not
  accelerate model generation.
- Preserved: compact Router prompts (`ROUTER_MAX_TOKENS=400`,
  `ROUTER_TEXT_CHARS=2000`), `disable_reasoning=True`, OCR + result caches,
  clean-result Judge skipping, concurrency 1.
- `validate_timeout_config()` (`app/core/config.py`) warns at startup when a
  request timeout exceeds a stage limit or leaves no room for one retry.
  `DocumentExtractionService` logs the effective budget
  (request/router/extractor/judge/concurrency/model, no content/credentials).
- LLM logs (`app/services/client.py`) now carry model, attempt number,
  request duration, and timeout category (`timeout` vs `rate_limit`) only —
  never document text or credentials. Timeout exhaustion, budget exhaustion,
  and stage-deadline skips each log duration + category.
- Cancellation releases the request slot: the semaphore is held via
  `limited_generation` across backoff/fallbacks and released on
  `asyncio.CancelledError` (covered by
  `test_shared_limiter_across_clients_and_cancellation` +
  new `test_cancellation_releases_request_slot`).

## Model measurements (caching bypassed where noted)

All timings are monotonic, live NVIDIA, this host/run — no speedup % claimed
without a comparable baseline.

| Model | Router probe (tiny prompt) | Router on 4 gold samples | Outcome |
|---|---|---|---|
| `meta/llama-3.2-90b-vision-instruct` (previous runtime) | 92.8s timeout (45 + 2 + 45, 1 retry, then `LLM request timed out`) | Not run to completion (same stall) | Fails Router; 200s stage only bounded the failure |
| `meta/llama-3.3-70b-instruct` (candidate, registry default) | 1.2s `410 Gone`: “reached end of life on 2026-08-26” | Same 410, no pages scored | **Not adopted** — EOL, fails fast |
| `meta/llama-3.2-11b-vision-instruct` (adopted) | 3.4s OK | 4/4 routing correct: Invoice1 3.2s, THAI_RECEIPT 2.2s, Delivery1 1.7s, PO 1.7s | Adopted for runtime + registry default (see below) |
| `nvidia/nemotron-3.5-lightning-30b-a3b` | 1.1s OK (tiny) / 1.8–24.6s on gold Router | 4/4 routing correct fresh-process | Fast but less consistent on Thai (24.6s); not adopted |

Full-pipeline eval with adopted 11b (`run_eval.py`, 8 files, result cache
enabled per script default — Router timings above bypassed cache by calling
agents directly; full pages below include cache + OCR):

- Router: fast (≈2s) and accurate (4/4).
- Extractor: mixed — simple docs succeed within 45s (Delivery1 F1 0.19,
  PO F1 0.571, judge timed out but page returned), complex invoices time out
  at 45s (Invoice1, THAI receipts: `attempt 45s → retry 45s → timed out`,
  extractor 92–132s within 150s limit, page failed). 45s bounds the failure
  but does not make a slow generation fast — complex extractor needs >45s on
  this model/host.
- Judge: 45s also tight (92s timeouts on some pages, page still returned with
  `judge_status=unavailable`).

Adoption decision: candidate 3.3-70b **not adopted** (EOL, 0 pages). Runtime
and registry default moved to 11b-vision (same llama-3.2 family, measured
Router 4/4 at ~2s vs 90b 92s timeouts, no increased routing errors). Field
accuracy on complex pages is still bounded by extractor speed — recorded
above, not claimed as improved.

## Recovery behavior

- Timeout retry: one same-tier retry after 2s within the 4-attempt budget;
  exhaustion stops the generation, never changes format. Stage deadlines stay
  authoritative (retry waits that cannot fit fail immediately).
- History retry: downloads the stored original(s) via `GET /sources/{id}`,
  groups by `download_url` (a multipage PDF shares one `source_id`, so one
  download resubmits the entire original), submits through `POST /extract`
  with the selected `force_refresh` / `disable_caches` flags, and polls the
  new job. Unavailable originals (404/empty/cleared history) show:
  “Original … is no longer stored … Re-upload the file to retry.”
- Failed-routing labels: API enum unchanged (`invoice` placeholder when no
  routing ran); UI shows **Unclassified** for `failed_stage` in
  (`ocr`, `router`) via `getDisplayDocType()` (badge, page tabs, History
  table). Extractor+ failures keep the routed type.

## Files changed

- `api/app/core/config.py` — 45s request default, retry-room comment,
  `validate_timeout_config()`, NVIDIA default → 11b-vision with EOL note.
- `api/.env.example` — explicit 45/100/150/100 + budget comment.
- `api/app/services/client.py` — model/attempt/duration/category logs,
  final-timeout log, no bodies/credentials.
- `api/app/services/extraction_service.py` — startup budget log + warning.
- `web/src/utils/pipeline.ts` — `getDisplayDocType()`.
- `web/src/components/ExtractionTab.tsx` — Unclassified badge/tabs.
- `web/src/components/HistoryTab.tsx` — real resubmission + Unclassified
  table + actionable errors.
- `web/src/api/client.ts` — `downloadOriginal()`.
- Tests: `test_timeout_config.py` (new), `test_client_timeout.py`
  (+ logging/cancellation), `test_llm_provider_config.py` (default update),
  `web/tests/pipelineStage.test.mjs` (+ Unclassified).

## Verification

- `ruff check api/` clean.
- `pytest api/tests/ -q` — full suite (new tests included).
- `npm test` (web) + `npm run build` clean.
- Live NVIDIA probes above (no credentials in logs/reports).
- Blocked / not claimed: unmeasured speedup %; complex-extractor latency on
  faster hosts; browser History-retry flow against live backend.
