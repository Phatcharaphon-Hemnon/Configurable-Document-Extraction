# Local extractor timeout — Phases 1–4 implementation report (2026-09-16)

Branch: `perf/latency-opt-20260914`. Failing page: `sroie_X51008142033.jpg`,
job `334f03b7-de2e-49d7-9c18-79ea243926cc` (ollama-local / qwen2.5:3b).

These are **correctness and observability changes, not a proven timeout or
speed fix**. No live inference was executed; no timeouts raised, no output
budgets cut, no model/provider switched, no validation weakened,
concurrency unchanged (1), regions still default-off.

## 1. Files changed and reasons

### ECC integration (new, project-local; upstream MIT preserved)
- `opencode.json` — registers 5 namespaced commands only. No model,
  provider, permission, hook, or plugin keys.
- `.opencode/ecc/commands/ecc-{debug,tdd,code-review,verify,update-docs}.md`
  — project adaptations for Python/FastAPI + React (pytest/ruff/npm,
  SQLite history, offline-first, no gold leakage). NOT upstream commands.
- `.opencode/ecc/PROVENANCE.md`, `.opencode/ecc/LICENSE.ecc-upstream`
  — source (github.com/affaan-m/ECC `.opencode` tree, fetched 2026-09-16:
  README, opencode.json, commands/{plan,tdd,code-review,verify,update-docs}.md,
  LICENSE), MIT notice verbatim, adapted-vs-upstream distinction.
- Runtime registration via installed OpenCode CLI at session start is
  **unverified** (JSON validity + template paths validated with the repo
  Python; no live session check performed).

### Observability + correctness (implementation)
- `api/app/services/request_control.py` — `DispatchEvent` (stage/model/
  tier/purpose/duration/outcome; content-free), task-local ledger
  (`collect_dispatches`, nesting-safe), `record_dispatch` (no-op without a
  collector), `summarize_dispatches` (dispatches/retries/fallbacks/
  corrections/timeouts per stage), `attempt_durations`, `new_dispatch_call`,
  `dispatch_tier/purpose/call` ContextVars.
- `api/app/services/client.py` — one ledger event per outbound HTTP attempt
  in `_chat_with_retry` (ok/timeout/rate_limited/error); call ids set per
  `generate_*` invocation; purpose labels initial→fallback→correction in
  `generate_structured` (+ vision legacy path) via `_call_tier` tier scope
  and `_generate_structured_inner` / `_generate_vision_inner` split
  (behavior-preserving; pure refactor + context tokens).
- `api/app/schemas/documents.py` — `RegionPageOutcome`
  (document/first_region_elapsed/complete_for_cache/path_detail);
  `ExtractionResult.diagnostics` (content-free sizes/settings/path, default
  `{}` so legacy payloads load); `JobProgress.dispatches` (default 0).
- `api/app/services/extraction_service.py`
  - `_extract_page_with_regions` returns `RegionPageOutcome` on all 5 paths
    via `_regions_inner`; splitter-fallback 4-tuple unpack defect fixed
    (fallback delegates to `_extract_one_page`, `complete_for_cache=True`,
    `path_detail="region fallback: <reason>"`).
  - Per-region ledgers replace shared `last_attempts` reset/read deltas;
    exactly-once debit (fixes empty-region double count structurally);
    checkpoint payloads gain `attempt_durations` + per-call `dispatches`
    summary; section details gain dispatch counts.
  - `_extract_one_page` aggregates ledger usage per stage (legacy
    `last_attempts` fallback when the ledger is empty = mocked agents) and
    writes `{stage}_attempt_{i}` durations into timings.
  - Full-page + region-fallback callers accumulate `progress["dispatches"]`
    (previously stuck at 0 off the region path) and attach `diagnostics`.
  - `_provider_error_details`: `StageTimeoutError` → typed details
    (`error_type="StageTimeoutError"`, stage + timeout in message);
    `CancelledError` → None anywhere in the chain (never a timeout).
- `api/app/guards/timeout_guard.py` — `track()` re-raises `CancelledError`
  when the task has pending cancels instead of converting to
  `StageTimeoutError` (outer cancel arriving with the deadline).
- `api/tests/test_dispatch_observability.py` (new, 14 tests) — RED-first
  regressions for all of the above (mocked transports/agents, temp
  storage; no live inference, no gold input).
- `api/scripts/diag_extractor_single.py` (new, PREPARED ONLY — never run) —
  Phase 5 single-dispatch Extractor diagnostic (see §6).

## 2. Corrected evidence and uncertainties

Stored job evidence (`data-local/extraction.db`, read-only):
- Job `334f03b7…`: `sroie_X51008142033.jpg`, 184361 bytes, page 1/1, status
  `completed` at job level but document `failed_stage=extractor`,
  `Extractor failed: LLM request timed out`, 0 fields/tables,
  `judge_status=unavailable`. **Do not treat job-level `completed` as
  success; never cache as completed.**
- Timings: router 53.47s, extractor 92.04s, pipeline 145.69s, ocr 2.09s +
  render 0.30s. Usage: router `{732 tokens, attempts 2, retries 1}`,
  extractor `{attempts 2, retries 1, NO token counts}`.
- Progress `dispatches=0` (full-page path never published counts — fixed).
- `full_text` 682 chars, 109 tesseract blocks preserved.

Corrections to the prior audit:
- **Output budget**: code default `EXTRACTION_MAX_TOKENS=8000`
  (`config.py:180`); effective setting then and now `3000`
  (`api/.env` + `api/.env.example:72`). The “8000” was the code default,
  “3000” the effective value. Settings at failure time remain **unknown**
  (not stored with the job) — do not back-apply either.
- **Timeout language**: extractor usage has no token counts = “no completed
  response/usage recorded”, NOT “zero bytes / zero tokens”.
- **Attribution**: extractor 92.04s < 150s stage limit with attempts=2 under
  the 45s request timeout ⇒ request-timeout path (45s + backoff + 45s),
  not the stage guard. Null status/code alone proves nothing; the
  exception/deadline/timing combination does.
- **Identity limits**: mtimes predating process start support inference,
  not proof, of loaded code. PID/cwd/start must be re-captured in build
  verification; no restart was performed.
- **Job deadline**: none exists in this service (request vs stage is the
  complete timeout attribution) — documented in code.
- Permanently unknown for this job: queue wait, per-attempt durations,
  prompt/schema token sizes, model-load/memory at failure time
  (inspection-time: swap exhausted, `ollama ps` empty — not failure-time
  evidence).

## 3. Confirmed defects and fixes

1. **Region fallback unpack defect** — `_extract_page_with_regions`
   returned a 4-tuple on the splitter-fallback path while the caller
   unpacked 3 ⇒ `ValueError` masked as “Page pipeline failed”. Fixed with
   `RegionPageOutcome` on all 5 return paths + caller.
2. **Shared `last_attempts` dispatch accounting** — reset/read deltas on a
   shared Client are race-prone and never published on the full-page path.
   Fixed with request-local ledger events at the transport boundary;
   exact retries/fallbacks/corrections/timeouts per stage.
3. **Empty-region double debit** (latent in the same area; caught by
   `test_empty_region_debits_budget_exactly_once` during this task) —
   success path debited, then the empty-output raise debited again in the
   except handler. Fixed by single accounting point after call capture.
4. **Missing timeout-source distinction** — stage-deadline expiry produced
   no `error_details`; cancellation could surface as timeout at the
   deadline race. Fixed: typed `StageTimeoutError` details, cancel guard
   in `track()`, `CancelledError` never yields provider details.
5. **Missing per-page/stage diagnostics** — fixed: durations in `timings`
   (`{stage}_attempt_{i}`, region keys), counts in `usage`
   (attempts/retries/fallbacks/corrections/timeouts/dispatches),
   sizes/fingerprints/settings/path in `diagnostics`, `dispatches` in
   `JobProgress` polling.

## 4. Exact commands/tests executed and outcomes

- `ruff check api/` → **All checks passed** (2 F401s introduced mid-task
  fixed: unused `_vtime` import, unused `stage_context` import).
- `python -m pytest api/tests/test_dispatch_observability.py` → **14 passed**
  (new file).
- `python -m pytest api/tests/test_dispatch_observability.py
  api/tests/test_region_pipeline.py
  api/tests/test_stage_failure_timing.py` → **33 passed**.
- `python -m pytest api/tests/` (full backend) → **583 passed, 2 skipped**,
  146 warnings (pre-existing pydantic `utcnow` deprecations).
- Mid-task regression found+fixed: `test_empty_region_debits_budget_exactly_once`
  (dispatches 20.0 vs calls×4=16) — double-debit from my first restructure;
  fixed via exactly-once accounting; full suite green after.
- Frontend: unchanged (no UI code touched) — `npm run build` not run;
  `JobProgress.dispatches` is additive JSON the UI safely ignores.
- Pre-existing LSP strictness diagnostics (Optional narrowing,
  `fingerprint_page` on Optional, `_json` possibly-unbound at
  `client.py:613`, capability-key tuple widths) predate this task and are
  untouched; repo gates (ruff + pytest) pass.

## 5. Review findings and remaining limitations

ECC workflow `/ecc-code-review` (project template) applied to my hunks:
- No shared mutable counters remain on the dispatch path (ContextVars;
  `collect_dispatches` restores tokens; `record_dispatch` never raises).
- No double-debit (single accounting point; regression test pins it).
- No cancellation misclassification (re-raise before debit; guard +
  details tests).
- No cache-eligibility change (failed ⇒ `is_cacheable_result False`,
  unchanged code path; test pins it).
- Page isolation intact (per-page ledgers/results; job-level dispatches
  accumulate intentionally); legacy payloads load (test pins it).
- No provider/model/budget/validation/concurrency/regions-default changes;
  no secrets in logs (identity only); no gold input anywhere; tests use
  temp storage (conftest isolation).
- Fixed within review: dead helper `stage_field_names` removed;
  `RegionPageOutcome` kwarg-plumbing simplified to a module import.

Limitations / unresolved hypotheses:
- The **root cause of the 2026-09-15 stall** (provider-side stall vs
  resource pressure on `qwen2.5:3b`) is still unresolved — per-attempt
  evidence for that night is unrecoverable. The bounded Phase 5 run is
  the instrument to resolve it.
- `last_attempts`/`last_usage` attributes remain (legacy fallback for
  mocked agents + trace generations); not removed to avoid breaking
  doubles.
- OpenCode command registration at session start is unverified (JSON +
  paths validated only).

## 6. Prepared diagnostic (NOT executed)

`api/scripts/diag_extractor_single.py` — Extractor-only, stored-OCR reuse:
- Gate (exit 2, no request): job row + page payload present; source
  original exists with matching byte size; stored text + blocks present.
  Verified today (read-only): original 184361 bytes = job size; 682 text
  chars + 109 tesseract blocks; page preview present.
- Caps: exactly ONE HTTP dispatch (budget patched in-process to
  1 attempt / 0 timeout-retries + wrapper aborting any 2nd dispatch);
  no Router/Judge; no format fallback; no corrective generation (parse
  failure raises); request timeout from effective settings (45s), stage
  150s, overall 180s.
- Isolation: temp catalog copy (live overlay untouched); temp workdir;
  nothing written to History/sources/caches; report to
  `/tmp/diag_extractor_single.json` (+ stdout).
- Records: effective settings (names + numeric limits, no secrets),
  `ollama /api/ps` before/after, `/proc/meminfo` before/after, ledger
  dispatch summary + per-attempt tier/purpose/duration/outcome,
  field/table counts or exception chain. Judge unavailable;
  cache-ineligible by construction. Labeled LLM-path diagnostic, not a
  benchmark.
- Command (DO NOT RUN without authorization):
  `source .venv/bin/activate && python api/scripts/diag_extractor_single.py
  --job-id 334f03b7-de2e-49d7-9c18-79ea243926cc`
- Blocked prerequisites if any gate fails at runtime: source-moved,
  DB-moved, catalog-missing (script reports `blocked` instead of
  substituting dataset transcripts — box/entity files are never read).

## 7. Rollback (this task only)

- Implementation: revert changes to `api/app/services/{request_control,
  client,extraction_service}.py`, `api/app/schemas/documents.py`,
  `api/app/guards/timeout_guard.py`; delete
  `api/tests/test_dispatch_observability.py` and
  `api/scripts/diag_extractor_single.py`.
- Integration: delete `opencode.json` and `.opencode/` (no pre-existing
  config was merged/overwritten).
- Untouched: everything else in the working tree (pre-existing branch
  diff, runtime DBs, sources, caches, ground truth).

## 8. Workflows consulted vs actually run

- Consulted (project templates): `/ecc-debug`, `/ecc-tdd`, `/ecc-code-review`,
  `/ecc-verify`, `/ecc-update-docs` (upstream ECC `plan/tdd/code-review/
  verify/update-docs` used as structural reference only; not vendored).
- Actually run: `ruff check api/`; focused + full `pytest` suites listed
  in §4 with real output; read-only DB/log inspection via sqlite3.
- Not run: the Phase 5 diagnostic script; frontend build (no UI changes);
  any live inference, restart, download, History deletion, or push.

## 9. Corrected implementation scope (addendum, 2026-09-16)

The region work delivered **region-specific prompt guidance with the full
schema/catalog retained** — not narrower wire contracts:

- Delivered: kind-specific output contracts per splitter kind
  (`REGION_KIND_CONTRACTS`, `build_region_request_text`,
  `REGION_REQUEST_VERSION = "region-request-v1"` in
  `api/app/services/region_requests.py`; reference
  `docs/reference/region_requests.md`). The wire schema stays the full
  `ExtractionResponseSchema`, the full catalog stays allowed in every
  region, region bodies/row boundaries/context notes travel verbatim.
- Deferred: per-kind wire-schema/catalog narrowing, with rationale
  (splitter table bands can hold the only printed instance of a scalar;
  merge, reconciliation, validator, and Judge all consume the full typed
  contract; the preserve-list outranks the scoping clause). Recorded in
  `docs/reports/compact_request_2026-09-16.md` §4.
- Passing correctness tests (`test_region_requests.py`,
  `test_region_budget_boundary.py`, and §11 below) establish request
  construction, budget enforcement, and merge/evidence behavior under
  mocks — they do **not** establish delivery of narrower wire contracts
  or measured speed improvements. Offline size reports carry no latency
  claim by construction (`summarize_region_requests`).

## 10. Reconciled experiment history (scripts/versions/dates)

"PREPARED ONLY — never run" in §6 above was true when written (file mtime
2026-09-16 03:41) and is now stale for the full-page scripts. Working-tree
mtimes + report contents give this order (all 2026-09-16, +07:00):

| Time | Artifact | Status |
|---|---|---|
| 03:41 | this report (§§1–8) | `diag_extractor_single.py` prepared, not yet run |
| ~04:05 | `docs/reports/diag_extractor_single_2026-09-16.md` | single EXECUTED: `failed-one-dispatch`, 45.059s request-timeout |
| 04:08 | `docs/reports/diag_extractor_stream_proposal_2026-09-16.md` | proposal only, not authorized, not executed |
| ~04:22 | `docs/reports/diag_extractor_stream_2026-09-16.md` | stream EXECUTED: `dispatch-failed`, 150.002s stage deadline |
| 04:32–04:33 | `api/scripts/diag_extractor_{stream,single}.py`, `streaming_diag.py`, `test_streaming_diag.py` (+3 tests) | offline post-run fixes, no dispatch |
| 04:43 | `docs/reports/compact_request_2026-09-16.md` | implementation record, no inference |
| ~04:50 | `docs/reports/compact_request_ab_2026-09-16.md` | full-page A/B EXECUTED (2 dispatches: 45.042s / 45.067s, both `failed-one-dispatch`; cold-vs-warm + KV-cache confounds recorded) |
| 04:56 / 07:52–07:53 | `region_requests.py` / `region_budget_boundary_2026-09-16.md` | region guidance + per-boundary budgets, mocked tests only |

"Never run" now applies ONLY to these specific unexecuted revisions:

1. Revised post-audit `diag_extractor_stream.py` (establishment timestamp
   + remainder overall budget + SDK-timeout wording) — next single
   experiment requires fresh authorization (stream report §5 addendum).
2. Revised post-fix `diag_extractor_single.py` (ledger fix) re-run —
   requires fresh authorization (single report addendum item 5).
3. `api/scripts/diag_region_pilot.py` (this task, §11) — prepared only.

Original measurements preserved as-is (nothing back-filled): single-run
45.059s with `ClientError → TimeoutError → TimeoutError →
CancelledError`; stream-run 150.002s bare `TimeoutError`, 0 chunks, usage
unavailable; A/B server tokens 2090 (cold, mid-eval cancel) vs 1322
(1142 cached, 338 generated before cancel); cold model load ~4s;
2084-token prompt at ~28 tok/s. Reconstruction caveats stand (single-run
tier/purpose detail is labeled reconstruction; stream header timing is
`unknown`, not "never").

## 11. Region pilot harness (this task — prepared only, not executed)

A full-page A/B harness does **not** count as a region pilot (full-page
Extractor calls only: no split/merge/reconcile, no per-region budgets or
checkpoints). New operator-only harness drives the actual region
orchestration on one provenance-verified page:

- `api/scripts/diag_region_pilot.py` (new) — calls
  `DocumentExtractionService._extract_page_with_regions` (split →
  per-region extract with checkpoints → merge → reconcile → shared
  validator + Judge finish) on the failing page's stored OCR.
  Provenance gate reused from `diag_extractor_single.py` (job row + page
  payload + source bytes + stored text/blocks; exit 2, no request, on any
  mismatch); dataset box/entity transcripts are never read (pinned by
  test). Sequential requests only, under the existing caps
  (per-generation 4-attempt budget, page 24, job 64; effective
  `LLM_MAX_CONCURRENT_REQUESTS` recorded). Explicit finite deadlines:
  request + stage limits from effective settings, harness overall
  deadline `--overall-deadline` (default 1800s = worst-case page
  24 × ~(45s + backoff) + router/judge + overhead — a bound, not a
  target). Isolated storage/catalog/checkpoints: temp catalog copy, temp
  SQLite history DB (`region_cache=True` there), temp sources/cache,
  completed-result cache OFF, live DB opened `mode=ro` only, report to
  `/tmp/diag_region_pilot.json` + stdout.
- Records effective flags (incl. the process-local
  `region_extraction_enabled=True` override against the production
  default `false`) and every actual region request shape (chars + sha256,
  never bodies; rebuilt-shape cross-check). Measures first
  deterministically validated usable region (post-hoc validator pass over
  isolated checkpoints; an unevidenced "successful" call is NOT usable)
  separately from final page completion (`first_region_elapsed` vs
  `page_total_s`). Preserves failures, unresolved regions, coverage
  (unresolved/ambiguous), and evidence findings (rejected counts, review
  kinds, acceptance status). Judge included by construction via the shared
  finish path, its dispatches counted in the reported total, and the
  omitted/unavailable-Judge-never-passed invariant is checked, not
  assumed.
- `api/tests/test_region_pilot.py` (new, 9 tests, offline mocked, temp
  storage only): partial success preserves first usable + forces review
  with explicit failed/unresolved states and `complete_for_cache False`;
  budget exhaustion yields explicit budget status with validated partial
  kept; all-failed yields no usable region with Judge never passed;
  unavailable Judge never passed (plus pure invariant unit checks);
  first-usable skips an unevidenced first region; shape match/mismatch
  accounting; budget gate arithmetic; no dataset-annotation inputs.

## 12. Confirmed defects and fixes (this task only)

1. **Pilot OCR-uncertainty derivation** — first draft read
   `ocr_page.review_reasons`, which is never stored on result payloads
   (always `False`). Fixed to the stored signal the merge layer uses:
   any block `review_reason` (`diag_region_pilot.py`). Caught in
   self-review before any run.
2. Review cleanups: import sorting (I001), one unused import (F401), a
   leftover expression in shape matching, and full extract_call signature
   on the request-shape recorder. No production files touched.

## 13. Exact commands/tests executed and outcomes (real output)

- `ruff check api/scripts/diag_region_pilot.py
  api/tests/test_region_pilot.py` → **All checks passed** (after fixing
  the I001 + F401 above).
- `python -m pytest api/tests/test_region_pilot.py -q` → **9 passed**
  (one initial failure fixed honestly:
  `test_pilot_first_usable_skips_unevidenced_region` used an evidence span
  absent from that region's own text; fixed with per-region own-text
  evidence; re-green, no assertion weakened).
- `ruff check api/` → **All checks passed**.
- `python -m pytest api/tests/ -q` (full backend) → **663 passed,
  2 skipped** (pre-existing skips), 160 warnings (pre-existing pydantic
  `utcnow` deprecations). Baseline before this task: 654 passed + same
  2 skipped; delta is exactly the 9 new tests.
- Frontend unchanged (no UI code touched) — `npm run build` not run.

## 14. Review findings and remaining limitations

`/ecc-code-review` checklist applied to the two new files: no shared
mutable counters (temp service per run; production budget scoping
untouched), no dispatch-accounting change (read-only reads of
usage/checkpoints), no cancellation reclassification (harness-level
overall expiry reported as `overall-deadline-expired`, distinct from
stage/request timeouts), no schema/cache-eligibility change, no secrets
in logs (names + numerics only), no gold/box inputs (test-pinned),
sanitizer path unchanged (actual orchestration), no
provider/model/budget/validation/concurrency/default changes, no
runtime-storage writes outside temp dirs, no unrelated files touched.
Decision: **pass** with the limitations below.

Limitations: harness prepared only (no live inference authorized — no
speed/quality claim exists for the region path); first-usable timing
combines the service-measured first completion with deterministic
post-hoc validation (validation time itself is offline, not a live
per-region timestamp); production `DispatchBudget` scopes region calls
only — Router/Judge dispatches are counted in the pilot total and checked
against caps informationally (no production change made); overall 1800s
is a generous finite bound; single-page pilot on the known failing page;
outer operator-shutdown CancelledError propagates (never converted).

## 15. Prepared diagnostic command, caps, isolation, blocked prerequisites

Command (DO NOT RUN without authorization):

```bash
source .venv/bin/activate
python api/scripts/diag_region_pilot.py \
    --job-id 334f03b7-de2e-49d7-9c18-79ea243926cc \
    --out /tmp/diag_region_pilot.json
```

Optional: `--doc-type invoice` (bypass Router, recorded) or omit (Router
runs, counted); `--no-region-contracts` for the legacy task-text path
(default is contracts on, recorded). Caps: sequential region calls, each
within the shared 4-attempt generation budget, page ceiling 24, job
ceiling 64; Router + Judge counted in the reported total. Isolation: temp
catalog/DB/sources/cache, live DB read-only, no completed-result writes
(checkpoints in temp DB only), report to `/tmp` + stdout. Blocked
prerequisites (exit 2, no request): source-moved, DB-moved,
catalog-missing, stored-payload drift.

## 16. Rollback (this task only)

- Delete `api/scripts/diag_region_pilot.py` and
  `api/tests/test_region_pilot.py`.
- This report: remove §§9–17 below the §8 line; §§1–8 above are preserved
  byte-identical.
- Untouched: all other working-tree files, runtime DBs, sources, caches,
  ground truth, settings, services.

## 17. Workflows consulted vs actually run

- Consulted (project templates): `/ecc-code-review`, `/ecc-verify`,
  `/ecc-update-docs` (structural reference only).
- Actually run: `ruff check` (focused + `api/`); focused
  (`test_region_pilot.py`, 9 passed) + full `pytest` (663 passed,
  2 skipped) with real output above; read-only code/doc inspection;
  offline no-inference geometry check for test fixtures.
- Not run: the region pilot harness; any live inference; frontend build
  (no UI changes); restarts, downloads, History changes, or pushes.
