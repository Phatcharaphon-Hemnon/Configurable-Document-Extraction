# Calibration ledger gap on the failed_stage path — fix (2026-09-16)

Offline implementation only. No live inference rerun, no production-config
changes, no service restarts, no downloads, no History changes, no pushes.

## 1. Corrected verification status of the 2026-09-16 smoke run

Split verdict on the durable-measurement smoke
(`--only sroie_X51008142033.jpg`, elapsed ~336s, artifacts preserved at
`/tmp/calib-verify-20260916T000000-smoke/` — read back 2026-09-16, intact:
`progress.jsonl` 2 lines, `measurements.json` 1 record,
`calibration_report.json` full report):

- **Report persistence: PASS.** Start intent persisted before inference,
  terminal outcome persisted, full report survived an inference timeout, no
  empty-over-evidence overwrite, no auto-resume. Unchanged by this task.
- **Measurement fidelity: FAIL (confirmed).** The surviving partial document
  carries `validation_errors: ["Extractor failed: LLM request timed out"]`
  and `judge_status: "unavailable"`, yet the record reports `dispatches: 0`,
  `timeouts: 0`, `errors: 0`, `attempt_outcomes: {}`, all stage seconds
  `0.0`, and the report states
  `"server_cancellation": "no timeout/cancel observed"` — contradicting its
  own validation error. Measured facts vs missing instrumentation:

  | Item | Value | Status |
  |---|---|---|
  | outcome / failed evidence | `partial` / extractor-timeout message | measured fact |
  | `total_seconds` | 334.21 | measured fact |
  | `dispatch_intents` | 2 (router + extractor presumed) | measured fact |
  | `dispatches_known` / terminal outcomes | 0 / `{}` | missing instrumentation (NOT zero) |
  | `router/extractor/judge_seconds` | 0.0 | missing instrumentation (NOT zero) |
  | `server_cancellation` | "no timeout/cancel observed" | wrong (derived from the empty ledger) |

  The historical artifacts are preserved as-is. They were NOT backfilled
  from the new mocked tests below — the smoke run's unknowns stay unknown.

## 2. Focused-test count reconciliation (exact commands)

Claim under review: "56 passed" for the 4-file focused selection vs
"35 locally verified".

- `python -m pytest api/tests/test_timeout_calibration_progress.py
  api/tests/test_timeout_calibration.py api/tests/test_dispatch_observability.py
  api/tests/test_http_budget.py -q` → **56 passed** (reproduced 2026-09-16
  on the current tree; per-file collection: 19 + 16 + 17 + 4 = 56).
- `35` = the first two files only
  (`test_timeout_calibration_progress` 19 + `test_timeout_calibration` 16).
  Both numbers are correct for their selections; the discrepancy was
  selection scope, not a missing-tests defect.

## 3. Confirmed defects and fixes (in-scope files only)

- `api/app/services/request_control.py` — **nested collector shadowing.**
  Service stages collect in inner `collect_dispatches` scopes that replaced
  the harness outer ledger, so returned-failure evidence never reached the
  report. Fix: inner scopes propagate their events to the parent ledger on
  exit, on success and on exception (finally-safe). Same for
  `collect_dispatch_intents`. Task-local isolation preserved; each summary
  reads exactly one ledger, so nothing is double-counted (region path keeps
  its explicit `region_ledgers` aggregation; propagated copies only add
  ancestor visibility).
- `api/scripts/calibrate_timeouts.py` (`_run_one_doc`, `_not_attempted`,
  new `_fill_attempt_evidence` / `_mark_stage_seconds_unknown`) —
  **wrong timing source + fabricated zeros + thin error records.**
  Stage seconds were read from `diagnostics.timings`, a key
  `_page_diagnostics` never emits (schema: durations live on the document's
  top-level `timings`). Fix: read top-level `timings`/`usage`/`failed_stage`;
  per-stage status `measured / skipped / not_run / unknown` with null for
  unknown (zero only when the stage provably did not run, e.g. judge after
  an extractor failure, or never-started records). Censored (non-ok)
  attempt durations are reported under `censored_durations` with measured
  values but stay excluded from the completed samples in
  `attempt_durations`. Exception/deadline/cancel paths now carry the full
  record shape (timeouts/errors/outcomes/status) instead of zeros. No retry,
  fallback, correction, budget, validation, or outcome-mapping changes.
- `api/app/services/extraction_service.py` — **raised-path timing loss.**
  `_extract_one_page` now stashes measured stage-block durations on the
  exception (`page_stage_timings`; cancellation never annotated/converted),
  and the `extract_group` per-page error document carries them. Failure
  presentation, Judge-unavailable status, evidence/partial results, cache
  exclusion, legacy loading, provider/model/budgets/validation,
  concurrency, and regions default-off are unchanged.
- `api/tests/test_failed_stage_calibration.py` (new, 13 tests) — RED-first
  regressions: real pipeline timeout → `failed_stage=extractor` (not
  raised) with surviving attempt/stage evidence under nested scopes;
  harness records for returned-failure, raised, cancellation,
  pre-dispatch-failure, sequential-run isolation, store round-trip, and
  censored-statistics handling. RED run: 10 failed / 3 passed; GREEN run:
  13 passed.

Statistics preserved: timed-out attempts stay censored (counted, p95
withheld, no proposal derived, `--apply` refused on insufficient
evidence); unknown durations are excluded from calculations with their
count reported via `dispatches_uncertain`/censored counts.

## 4. Exact commands/tests executed and outcomes (real output)

- `ruff check api/` (incl. new test + all touched files) → All checks passed.
- New: `python -m pytest api/tests/test_failed_stage_calibration.py -q` →
  RED `10 failed, 3 passed`, GREEN `13 passed`.
- Focused existing: `test_timeout_calibration_progress` +
  `test_timeout_calibration` + `test_dispatch_observability` +
  `test_http_budget` + `test_stage_failure_timing` → **59 passed**.
- Full backend `python -m pytest api/tests/ -q` → **711 passed, 2 skipped**
  (698 pre-existing + 13 new; skips pre-existing). Frontend build skipped:
  no UI changed.

## 5. Review findings (ecc-code-review categories) and remaining gaps

- Correctness: no shared-state leakage (ContextVar chains, concurrent-task
  test green); no double counting (single-ledger summaries verified +
  sequential-run test); cancellation never converted (explicit re-raise +
  tests); region unpack/outcomes untouched; page isolation untouched;
  legacy loading green.
- Cache/failure: eligibility (`error`/`failed_stage`/`judge_status`) does
  not read `timings`, so error-doc timing carriage cannot make failures
  cacheable; failed_stage presentation and outcome mapping preserved.
- Security: no new logging (events stay content-free); test fixtures use
  synthetic text/bytes; gold JSON used for post-hoc scoring only (existing
  pattern); sanitizer and storage paths untouched.
- Scope: no provider/model/retry/fallback/correction/budget/validation/
  concurrency/regions changes; unrelated working-tree changes preserved
  (pre-existing `M`/`D` entries and untracked reports untouched).
- Remaining gaps: (a) the historical smoke record keeps its zeros/unknowns
  by design (no backfill); (b) `extract_group` raised-error documents have
  no `failed_stage`, so the harness maps them `completed` when no failure
  flag exists — presentation preserved deliberately, flagged for a future
  task; (c) kill-mid-run durability remains test-suite-only evidence (no
  live kill test per offline constraint); (d) stage-vs-attempt magnitude
  calibration still needs live data — no timeout values are proposed or
  changed here.

## 6. Rollback (this task only)

- `git checkout HEAD -- api/app/services/request_control.py
  api/app/services/extraction_service.py` (restores pre-existing
  working-tree state, not HEAD-clean upstream — these files carry prior
  uncommitted work this task did not author),
  then restore `api/scripts/calibrate_timeouts.py` hunks
  (`_fill_attempt_evidence`, `_mark_stage_seconds_unknown`,
  `_not_attempted` shape, success-path top-level timings) from editor
  history, `rm api/tests/test_failed_stage_calibration.py`, and
  `rm docs/reports/calibration_ledger_failed_stage_fix_2026-09-16.md`.
  Untracked prior-session reports and all other working-tree changes are
  unaffected. Suggested verification after rollback: focused 4-file
  selection (expect 56 passed) + full backend suite.

## 7. Workflows consulted vs checks actually run

- Consulted: project-local `/ecc-tdd` (RED→GREEN→REFACTOR cycle followed),
  `/ecc-code-review` (§5), `/ecc-verify` (§4), `/ecc-update-docs` (this
  report); `AGENTS.md` rules (field catalog, token minimalism,
  injection guard, evidence/confidence, Pydantic, palette N/A, docs rule).
- Actually run: `ruff`, new-test RED/GREEN runs, focused selections,
  full backend suite, `/tmp` smoke-artifact read-backs above. NOT run (out
  of scope / constrained): live inference rerun, kill/restart probes,
  frontend build, pushes, History/production changes.
