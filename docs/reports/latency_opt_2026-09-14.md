# Latency optimization on main without quality loss (2026-09-14)

Branch: `perf/latency-opt-20260914` (isolated from `main`; pre-existing
uncommitted router-timeout work preserved on the same branch, untouched by
this change set — see §7). `local-LLM` branches not touched.
HEAD commit: `f7a48f9` (plus uncommitted changes listed in §7).

Environment: 8 CPU, 7 GB RAM, Python 3.14 venv, local Tesseract
(`eng+tha`, 300 DPI) via `.local/ocr`, uvicorn-equivalent in-process service.
Effective provider/model (secret-safe, from `api/.env` identity only —
no key displayed): **`nvidia` / `meta/llama-3.2-11b-vision-instruct`**,
`LLM_REQUEST_TIMEOUT_SECONDS=45`, `ROUTER=100`/`EXTRACTOR=150`/`JUDGE=100`,
`LLM_MAX_CONCURRENT_REQUESTS=1` (default), `OCR_ENGINE=tesseract`,
`OCR_LANGUAGES=eng+tha`, `OCR_DPI=300`, `JUDGE_SKIP_WHEN_CLEAN=true`,
`JUDGE_SKIP_CONFIDENCE=0.85`, `ROUTER_MAX_TOKENS=400`,
`ROUTER_TEXT_CHARS=2000`, `FEW_SHOT_EXAMPLES_PER_DOC_TYPE=0`,
`TEMPORAL_ENABLED=false`. Provider/model unchanged by this work.

## 1. Confirmed causes (rechecked on current main)

1. **Whole-job `TypeError` retry** — `extraction_service.py::run_job`
   caught *any* `TypeError` from `extract_group()` and reran the entire job.
   The fallback existed only for 2-arg test doubles
   (`test_request_queue.py`). A real internal `TypeError` would silently
   duplicate all OCR + LLM work. Removed; doubles updated to the full
   signature. Same pattern fixed in
   `temporal/activities.py::extract_activity` (explicit double detection
   instead of `except TypeError`).
2. **OCR-before-first-result** — `extract_group()` awaited
   `LocalOCRClient.aparse_file()` (all pages) before extracting page 1, and
   read pages back via mutable `last_pages`. On a 3-page PDF page 1 could not
   checkpoint before ~all-page OCR finished. Fixed with page-at-a-time
   streaming (below); `last_pages` remains as an incrementally-grown
   compatibility view, new code uses yielded pages.
3. **Judge triple repetition** — `judge.py::JudgeAgent.evaluate` sent the
   prediction JSON + provenance lines + identifier lines (same values three
   times). Consolidated to one canonical record list; output contract and
   unknown-field guard unchanged.
4. **Already fixed on main, verified not regressed** — manifest fast path
   before `_job_lock`/OCR (`run_job::_try_fast_path`), explicit-`doc_type`
   Router bypass, shared HTTP clients, `max_retries=0`, unsupported-tier
   memory only on explicit rejection, ≤1 corrective generation, confirmed-
   truncation honesty, Judge skip gate. `client.py` untouched by this change
   set.

## 2. Changes and intended effects

| File | Change | Effect |
|---|---|---|
| `api/app/services/local_ocr.py` | `_ocr_cache_key()` shared helper; `_ocr_loaded_page()` single per-page implementation; new `aparse_pages()` async generator; `aparse_file()` collecting wrapper | Page 1 streams to extraction while later pages still OCR; identical cache keys/semantics |
| `api/app/services/extraction_service.py` | `_stream_ocr_pages()` (streams on unstubbed real client, honors `aparse_file` doubles); per-page loop over the stream; upfront `_count_pages` progress total; per-page checkpoint unchanged; `time_to_first_page` response timing; manifest uses stream count; **removed `except TypeError` whole-job retry** | First-result latency down, total unchanged (concurrency 1 kept); real failures fail honestly once |
| `api/app/agents/judge.py` | Canonical `field:`/`cell:` record list replaces prediction+provenance+ids triple; instruction + guard updated | Fewer judge input tokens, same evidence/role/row context |
| `api/app/services/result_cache.py` | `PROMPT_VERSION=prompts-v4-canonical-judge`, `JUDGE_VERSION=judge-v3-canonical` | Old page entries + manifests invalidate (both embed the versions); saved-result *loading* unaffected (output contract unchanged) |
| `api/app/temporal/activities.py` | `parse_detailed_activity` streams via `aparse_pages`; `extract_activity` explicit double branch, no `TypeError` catch | Same fixes on the Temporal path |
| `api/tests/test_latency_opt.py` (new, 8 tests) | No-retry-on-TypeError, no broad catch in activity, streaming order/incremental state, stubbed-`aparse_file` honored, first-page timing, partial preservation, judge canonical prompt + unknown-field guard, version-bump invalidation | Regression cover for every change |
| `api/tests/test_request_queue.py` | 3 doubles accept `**kwargs` | Compat with removal above |
| `api/tests/test_handwriting_ocr_recovery.py` | Change-detector expects `prompts-v4`; coherence-key check reads `aparse_pages`+`_ocr_cache_key` | Same coverage, new location |
| `docs/reference/page_streaming.md` (new) | Streaming design + timing keys + judge note | AGENTS.md rule 7 |

Deliberately NOT changed: timeouts, token limits, few-shot (0), reasoning
flags, skip-gate criteria, provider/model, concurrency (1), hybrid opt-in,
public API/frontend contracts.

## 3. Measurements (same documents, monotonic clocks)

Isolated storage everywhere (`tmp_path`/tempdirs; real History untouched).
OCR = real local Tesseract; "mocked LLM" rows stub Router/Extractor/Judge.

| Document | Run | Result |
|---|---|---|
| Invoice1.jpg (1pp) | Honest OCR, no cache (pre) | 5.62s (ocr 4.86 + render 0.55) |
| THAI_RECEIPT.jpg (1pp) | Honest OCR, no cache (pre) | 11.05s (ocr 9.37 + render 1.61) |
| Delivery1.webp (1pp) | Honest OCR, no cache (pre) | 3.65s (ocr 3.31 + render 0.20) |
| purchase_orders1.pdf (1pp) | Honest OCR, no cache (pre) | 2.33s (ocr 1.68 + render 0.40) |
| Invoice+purchase.pdf (3pp) | Honest OCR, no cache (pre) | 16.74s (ocr 14.81 + render 1.72) |
| Invoice1.jpg | Mocked warm, pre → post | 6.37s → **6.40s** (no regression) |
| Invoice1.jpg | Mocked repeat (fast path), pre → post | 0.79s → **0.72s** (lookup ~1ms, render ~0.7s; `<5s` target met) |
| Invoice+purchase.pdf (3pp) | Mocked warm, pre → post | 20.95s → **18.68s** (OCR variance; no regression) |
| Invoice+purchase.pdf (3pp) | Mocked repeat (fast path), pre → post | 2.04s → **1.76s** (`<5s` met) |
| Invoice+purchase.pdf (3pp) | **Streaming, honest OCR, mocked LLM (post)** | Page-1 checkpoint **8.50s** of 18.23s total (`time_to_first_page=8.5`); checkpoints at 8.50/16.04/18.23s — first result ~47% earlier, total unchanged as designed |
| Live Router (NVIDIA 11b, agents-direct, n=4) | Invoice1 17.6s invoice ✓, THAI_RECEIPT 8.3s invoice ✓, Delivery1 8.0s delivery_note ✓, PO 12.3s purchase_order ✓ | 4/4 correct; slower than the 2026-09-14 ~2s probes (provider load varies) |
| Live full page Delivery1 ×2 (isolated KB copy, caches off) | Router 5.9s/16.5s OK → **extractor 45+2+45=92.1s timeout → `failed_stage=extractor`, `judge_status=unavailable`** | Honest failure, budget honored (attempts=2/retries=1, no tier change); `<60s` new-document target **missed — provider-bound** (see §5) |

No median/p95: sample sizes are 1–2 per condition by design (bounded live
budget); individual values reported instead. Timing units verified
(seconds, `perf_counter`); `time_to_first_page` is job-relative and compared
only to `processing`, never added to nested stage timings.

## 4. Accuracy / parity

- Backend: **445 passed (436 + 9 eval), 2 skipped**; `ruff check api/` clean;
  web `npm test` 10/10, `npm run build` ok.
- Parity: pre-existing fast-path parity tests green (cached ≡ fresh modulo
  IDs/timings); judge output contract unchanged so old payloads still
  validate (change-detector test updated, invalidation test proves old keys
  miss after the bump).
- Live accuracy eval (`run_eval.py --all`) **blocked**: extractor stalls on
  the active model (2 consecutive 92s timeouts on the simplest fixture);
  running the full matrix would burn ~10+ min for mostly-timeout rows and
  prove nothing about the app changes. Ground-truth manifest is provisional
  (not independently adjudicated) in any case.
- Handwriting: coherence gate + RapidOCR recovery untouched; difficult
  handwriting stays a separate honest-block path (no new measurements taken).

## 5. Failures, misses, limitations

- **`<60s` new-document target missed on live runs**: 2× Delivery1 attempts
  failed at the extractor after the full 92s budget (router 6–16s + OCR ~3s
  + 92s extractor). Cause is provider-side generation stall on
  `nvidia/meta/llama-3.2-11b-vision-instruct` at measurement time (consistent
  with the 2026-09-14 report's "complex extractor needs >45s" finding), not
  the app changes (extractor runs before any changed code except streaming,
  which only helps). Not gamed: no timeout/content/review changes.
- Live Judge canonical-prompt verification blocked by the same stalls
  (judge never reached live); prompt content + guard covered by unit test.
- `cache_misses` counts pages even when the result cache is disabled
  (pre-existing accounting; status string still reports `disabled`).
- Rendering still runs up front per file (~0.5–0.7s/page); per-page render
  streaming is future work.

## 6. Commands run (actual outcomes)

- `ruff check api/` → clean (4 auto-fixed unused imports in new test file).
- `pytest api/tests/ -q` → 436 passed, 2 skipped; `test_run_eval.py` → 9 passed.
- `npm test` → 10/10; `npm run build` → ok (2.24s).
- `/tmp/opencode/baseline_pre.py`, `baseline_pre2.py`, `baseline_mocked.py`,
  `streaming_check.py`, `live_one.py` (×2) → values in §3.
- Browser upload/progress/review/export/cache-reuse checks against a live
  backend: **blocked** (no running backend in this environment; frontend
  build + unit tests pass).

## 7. Scope note, rollback, invalidation

This branch also carries the pre-existing uncommitted router-timeout work
(`config.py`, `client.py`, `.env.example`, `web/*`, `test_timeout_config.py`,
`test_client_timeout.py`, `router_timeout_nvidia_2026-09-14.md`) — preserved,
not authored here. This change set's files: `api/app/agents/judge.py`,
`api/app/services/{extraction_service,local_ocr,result_cache}.py`,
`api/app/temporal/activities.py`, `api/tests/{test_latency_opt.py (new),
test_request_queue.py, test_handwriting_ocr_recovery.py}`,
`docs/reference/page_streaming.md` (+ this report).

- Rollback: `git checkout main -- <files above>` (or drop the branch); runtime
  kill-switch `RESULT_CACHE_ENABLED=false` disables fast path + manifests
  (normal path unchanged); `force_refresh`/`disable_caches` semantics kept.
- Invalidation: `PROMPT_VERSION`/`JUDGE_VERSION` bumps change both page
  fingerprints and manifest keys → old entries/manifests resolve as misses
  (proven by `test_prompt_version_bump_invalidates_manifest_and_entries`);
  TTL (7d), 128-entry eviction, atomic SQLite writes, restart persistence,
  corruption-as-miss unchanged.
