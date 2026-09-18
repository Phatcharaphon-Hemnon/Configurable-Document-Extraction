# Ledger-fix verification smoke — one SROIE document (2026-09-17 local / 2026-09-16 UTC)

One authorized document execution: `sroie_X51008142033.jpg`, reusing the
verified harness (`api/scripts/calibrate_timeouts.py --only`), no additional
framework or planning round. No code, config, History, or production changes
by this task. No timeout values derived; no second run.

- Run stamp (UTC): `20260916T201707`. Isolated out dir:
  `/tmp/calib-verify-20260916T201707-ledgerfix-smoke/` (3 harness files only).
- Persistent evidence (sanitized): `logs/calibration-smoke-20260916T201707-ledgerfix/`
  (`measurements.json`, `progress.jsonl`, `calibration_report.json`,
  harness stderr as transport evidence, `run_meta.json`).
- Historical artifacts preserved untouched:
  `/tmp/calib-verify-20260916T000000-smoke/` (read back: same 3 files,
  Sep 17 02:19 timestamps, not backfilled, not deleted).
- `api/.env` byte-identical before/after
  (`sha256 7942bfbdc924bf87…`, `git status -- api/.env` clean).

## 1. Files changed and reasons (this task only)

None. Verification-only task. The ledger fix under test is the prior
`calibration_ledger_failed_stage_fix_2026-09-16` change set, confirmed
present in the working tree (`request_control.py` parent-ledger propagation
on scope exit; harness top-level `timings` source with null/unknown
semantics; raised-path stage-timing carriage in `extraction_service.py`).

## 2. Bounds compliance (all met, real observations)

| Bound | Observed | Status |
|---|---|---|
| ≤300s per inference attempt | router ok 101.73s; extractor timeout 300.1s (hit ceiling = first inference timeout, doc stopped) | met |
| ≤600s document processing | doc `total_seconds` 406.66 | met |
| ≤660s overall incl. preflight + cleanup | wall 411s (exit 0) | met |
| ≤3 inference HTTP dispatches | 2 (router + extractor; judge never dispatched) | met |
| No retries / fallbacks / corrections / confirm / apply | `retries 0, fallbacks 0, corrections 0`; `confirm []`; `applied {false, "not requested"}` | met |
| Stop after first inference timeout | extractor attempt 1 timed out → `partial`, judge `unavailable`, no further dispatch | met |
| Stop before dispatch if idleness unprovable | pre-dispatch: `/api/ps {"models":[]}`, `ollama ps` empty, no `llama-server` worker, no listener on :8000, History `queued/processing = []`, tags show `qwen2.5:3b` (no pull) | idleness established, dispatched |

CLI hardcodes `TASK_LIMIT_S=3600` / `MAX_DISPATCHES=60`, which exceed the
authorized overall/dispatch bounds — same as the prior smoke, compliance was
external: outer `timeout -s TERM 660s` wrapper (411s observed) plus
single-doc single-attempt scope (2 dispatches). No native-flag change made.

## 3. Separate verdicts

### 3a. Extraction outcome: `partial` (extractor timeout — measurement datum)

Single record: outcome `partial`, `validation_errors:
["Extractor failed: LLM request timed out"]`, `judge_status: unavailable`,
`n_fields 0`, accuracy `tp0/fp0/fn3` recorded without interpretation.
Timeout source is the inference-attempt transport timeout (extractor 300.1s
vs 300s ceiling, attempt 1 of 1) — not the 600s doc deadline (doc 406.66s)
and not the 660s outer bound (wall 411s).

### 3b. Persistence outcome: PASS

`progress.jsonl` has 2 lines: `doc_start` (phase sweep, index 0/1, cold)
persisted BEFORE inference, then `doc_outcome`. Full
`calibration_report.json` written on the exit-0 path (`task_elapsed_s`
406.67, `store_write_errors []`). Exactly 1 record, no auto-resume or
second run, no harness process lingering, nothing applied.

### 3c. Measurement-fidelity outcome: PASS (fix confirmed)

Record carries real evidence on every checklist item (prior smoke showed
`dispatches 0 / timeouts 0 / errors 0 / outcomes {} / all stages 0.0` with
the same extractor-timeout validation error):

- Document start saved before inference: yes (line 1 of `progress.jsonl`).
- Dispatch events survive nested collectors: yes — `dispatches 2`,
  `dispatches_known 2`, `dispatch_intents 2`, `terminal_by_outcome
  {ok: 1, timeout: 1}` (was 0/0/{} pre-fix).
- Successful and timed-out attempts retain measured durations: yes —
  `attempt_durations.router [101.73]` (ok) and
  `censored_durations.extractor [300.10]` (non-ok kept out of completed
  samples, never merged).
- Stage durations from the actual emitted contract: yes — top-level
  `timings`: ocr 3.57 / router 101.74 / extractor 300.14, all `measured`;
  judge `0.0` / `not_run` (provably never ran after the extractor failure —
  the only valid zero).
- Unknown values null, never fabricated zeros: yes — no `unknown` stage on
  this path needed nulls; extractor/judge summary quantiles are null with
  `p95_withheld: true` and explicit reasons.
- Timeout observations censored: yes — extractor p95 withheld
  (`n_censored 1`, reason "no completed samples"); router p95 101.73 from a
  single sample is flagged `provisional_tail: true`, candidate 127.16
  reported but NOT adopted (see §5).
- Raised errors with unknown stage explicitly unattributed: N/A on this
  path (returned-failure path, `failed_stage` extractor); harness maps
  raised-error documents without `failed_stage` as `completed` only when no
  failure flag exists — presentation preserved deliberately, flagged for a
  future task (carried over from the fix report).
- Counts/durations agree with independent transport/server evidence:
  `dispatches 2 / timeouts 1 / errors 0` agrees with harness stderr
  (`stage=extractor ... attempt=1 duration=300.1s category=timeout
  timeout_retries=0` + the matching `LLM request timed out` line);
  router stage-block 101.74 vs transport 101.73 and extractor stage-block
  300.14 vs censored 300.10 are coherent magnitudes (block = queue +
  attempts + backoff); total 406.66 ≈ 3.57+101.74+300.14 + ~1.2s overhead.
  `server_cancellation` now honestly reads "unknown — client timeout never
  proves server-side cancellation …" (was the false "no timeout/cancel
  observed" pre-fix). Post-run `/api/ps` shows `qwen2.5:3b` resident
  (CPU, context 4096, ~2 min expiry) — expected post-run residency, not a
  discrepancy; server-side completion/cancellation after the client bound
  remains unprovable, which is exactly what the report states. No
  discrepancies found; nothing guessed.

## 4. Exact commands/tests executed and outcomes (real output)

- `ruff check api/` → **All checks passed**.
- Focused (6 files): `test_failed_stage_calibration` +
  `test_timeout_calibration_progress` + `test_timeout_calibration` +
  `test_dispatch_observability` + `test_http_budget` +
  `test_stage_failure_timing` → **72 passed** (50.94s).
- Full backend `python -m pytest api/tests/ -q` → **711 passed, 2 skipped**
  (106s; skips pre-existing). Matches the fix report's 698 + 13 new.
- Frontend build: skipped — no UI changed (per `/ecc-verify` rule).
- Smoke: `timeout -s TERM 660s bash -c 'source .venv/bin/activate &&
  python api/scripts/calibrate_timeouts.py --out
  /tmp/calib-verify-20260916T201707-ledgerfix-smoke --only
  sroie_X51008142033.jpg'` → exit 0, wall 411s. Stdout summary:
  `total_dispatches 2 / uncertain 0`, outcomes `partial: 1`, candidates
  `router 127.16 / extractor null / judge null`, `confirm: []`,
  `applied: {false, "not requested"}`.
- Post-run probes (read-only): `/api/ps` (model resident, see §3c),
  `git status -- api/.env` (clean), historical-dir listing (intact),
  process check (no `calibrate_timeouts` lingering).

## 5. Review findings and remaining limitations

- The router "candidate" 127.16s (P95×1.25 from n=1, provisional tail) is a
  reported heuristic only: no `--confirm` ran, `confirm_ok false`, nothing
  applied, and per task order **no timeout value is derived from this
  single smoke test**. Its `retry_fit` honestly reports "exceeds" against
  the current 100s router stage limit.
- Kill-mid-run durability (exit 2/3 paths) remains test-suite-only evidence;
  only inference-timeout durability was demonstrated live (same scope as
  authorized — no kill test performed).
- Single-sample accuracy (`tp0/fp0/fn3`) is recorded without interpretation;
  longer timeouts are not described as improvements anywhere in this report.
- The historical smoke record keeps its zeros/unknowns by design (no
  backfill) — unknowns stay unknown.

## 6. Rollback (this task only)

Nothing to roll back: no code, config, or History changes. To reproduce:
re-run the §4 smoke command with a fresh `--out` dir. To clean up: `rm -rf
/tmp/calib-verify-20260916T201707-ledgerfix-smoke
logs/calibration-smoke-20260916T201707-ledgerfix` (historical
`/tmp/calib-verify-20260916T000000-smoke/` must be left intact).

## 7. Workflows consulted vs checks actually run

- Consulted: project-local `/ecc-verify` (§4 checks), `/ecc-update-docs`
  (this report), `AGENTS.md` rules, prior reports
  `calibration_ledger_failed_stage_fix_2026-09-16.md`,
  `durable_measurement_smoke_X51008142033_2026-09-16.md`,
  `timeout_calibration_2026-09-16.md` (context only — claims re-verified,
  not assumed).
- Actually run: `ruff`, focused pytest (6 files), full backend pytest,
  `/api/tags` + `/api/ps` + `ollama ps` + process/port/History read-only
  probes, source reads for bound verification, one bounded `--only` smoke
  run under `timeout 660s`, file read-backs above, sanitized persistence
  outside `/tmp`. NOT run (out of scope / constrained): live kill/restart
  probes, frontend build, second run, production-setting changes,
  downloads, History changes, pushes.
