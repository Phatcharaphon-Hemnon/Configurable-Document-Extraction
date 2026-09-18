# Durable-measurement smoke test — one SROIE document (2026-09-16)

Scope: verify durable progress persistence of `api/scripts/calibrate_timeouts.py`
(`--only sroie_X51008142033.jpg`), NOT derive production timeouts.
Branch `perf/latency-opt-20260914`, rev `7c6139b`. No code changed by this task.

## 1. Files changed and reasons

- None. No production, harness, config, or History changes.
- New file (this report) per `/ecc-update-docs`.
- New isolated artifacts only: `/tmp/calib-verify-20260916T000000-smoke/`
  (`progress.jsonl`, `measurements.json`, `calibration_report.json`).
- `api/.env` byte-identical before/after
  (`sha256 7942bfbdc924bf87…`, `git status -- api/.env` empty).

## 2. Pre-dispatch verification (real output)

- CLI bounds (`calibrate_timeouts.py:81-85`): `EXPERIMENT_REQUEST_S=300.0`,
  `DOC_LIMIT_S=600.0`, `EXPERIMENT_STAGE_S=550.0` — per-attempt/doc caps fit
  the authorized 300s/attempt, 600s/doc. `--only` exists with fail-fast on
  unknown ids (`--help` verified). No `--confirm`/`--apply` passed, so no
  confirmation or apply. Single-attempt patch (`install_single_attempt`:
  `TIMEOUT_MAX_RETRIES=0`, one strongest tier, no fallback/correction).
- Shared transport cap: `attempt_ceiling_s` ContextVar
  (`request_control.py:306`) + `effective_attempt_ceiling =
  min(request, doc-remaining, task-remaining)` (`timeout_calibration.py:70`),
  enforced at the transport boundary (`client.py:956-958` + `wait_for:1012`).
  Record shows `effective_request_s: 300.0`.
- Overall/task caps: CLI hardcodes `TASK_LIMIT_S=3600` and
  `MAX_DISPATCHES=60`, which EXCEED the authorized 660s-overall / 3-dispatch
  bounds — no native CLI flags tighten them. Compliance was achieved
  externally: outer `timeout -s TERM 660s` wrapper (overall 336s observed)
  plus single-doc single-attempt scope (intents 2, inferred actual 2; see §4).
  A strict native-flag reading remains unmet — recorded, not worked around.
- Isolation: fresh out dir (did not exist before the run); no writes outside
  it plus process-tmp (`calib-*`, auto-removed). Live History DB opened
  read-only; result cache / database / audit / Temporal off in-harness.
- Provider idleness (multi-source, not `/api/ps` alone):
  `/api/ps` = `{"models":[]}` AND `ollama ps` CLI table empty AND no
  `llama-server` worker process (only supervisor PID 510, uptime 6h36m) AND
  no listener on :8000 AND History read-only check `queued/processing = []`
  (9 terminal jobs) AND tags reachable with `qwen2.5:3b` present (no pull).
- Failed-sweep artifacts: `/tmp/calib-20260916/` ABSENT at pre-dispatch
  check (ephemeral `/tmp` cleared since the prior report) — could not be
  re-verified; preserved by using a new dir and never touching old paths.
- Effective settings (preflight + report): `ollama-local` / `qwen2.5:3b` @
  `http://localhost:11434/v1`, temp 0.0, `EXTRACTION_MAX_TOKENS=3000`,
  compact OFF, regions OFF, concurrency 1. Production timeouts unchanged:
  45 / router 100 / extractor 150 / judge 100.

## 3. Exact commands/tests executed and outcomes (real output)

- `ruff check api/` → **All checks passed**.
- Focused: `test_timeout_calibration_progress` + `test_timeout_calibration` +
  `test_dispatch_observability` + `test_http_budget` → **56 passed**.
- Full backend `python -m pytest api/tests/ -q` → **698 passed, 2 skipped**
  (pre-existing skips). Frontend build skipped: this task changed no UI.
- Smoke run:
  `timeout -s TERM 660s bash -c 'source .venv/bin/activate && python
  api/scripts/calibrate_timeouts.py --out
  /tmp/calib-verify-20260916T000000-smoke --only sroie_X51008142033.jpg'`
  → exit 0, elapsed 336s (within 660). Stdout summary:
  `total_dispatches 0 / uncertain 0`, outcomes `partial: 1`,
  candidates all null, `confirm: []`, `applied: {false, "not requested"}`.
  Harness stderr: `LLM timeout exhausted job=- stage=extractor
  model=qwen2.5:3b attempt=1 duration=300.1s category=timeout
  timeout_retries=0`; `LLM request timed out ... attempts=1 ... timeout=300s`.

## 4. Read-back of persisted files (observed result)

- `progress.jsonl` (2 lines): `doc_start` for `sroie_X51008142033.jpg`
  (phase sweep, index 0/1, cold) BEFORE inference, then `doc_outcome`.
  Start-intent-first ordering holds.
- `measurements.json`: 1 terminal record, outcome `partial`,
  `total_seconds 334.21`, `validation_errors: ["Extractor failed: LLM
  request timed out"]`, `judge_status: unavailable`, `n_fields 0`.
- `calibration_report.json`: full report written (exit-0 path);
  `task_elapsed_s 334.22`, `store_write_errors []`, no proposal derived
  (all P95 withheld — zero completed samples), nothing applied.
- Timeout source: inference-attempt transport timeout in the extractor
  (300.1s vs 300s ceiling, attempt 1 of 1). NOT the 600s doc deadline
  (doc 334s) and NOT the 660s outer bound (run 336s). Document stopped
  after its first inference timeout: no retry, no fallback/correction, no
  confirm, no apply; judge never dispatched (`unavailable`).
- Dispatch accounting: `dispatch_intents 2` (router + extractor presumed)
  vs `dispatches_known 0 / uncertain 0` — intent and observed transport
  events ARE distinguished, with the explicit note that zero terminal
  events means unknown, not zero. Inferred actual HTTP dispatches = 2
  (router success implied by reaching extractor + extractor 300.1s
  timeout from stdout) — within the 3-dispatch bound on every counting
  (0 counted, 2 intents, ~2 actual). No second run or resume:
  exactly 1 record, `confirm_ids []`, no harness process lingering,
  post-run `/api/ps` empty again.

## 5. Persistence verdict vs extraction outcome (separate)

- Durable-measurement smoke test: **PASS** — start record before
  inference; intent/observed separated; terminal outcome persisted;
  full report survived an inference timeout; no empty-over-evidence
  overwrite; no auto-resume or second run.
- Extraction outcome for this document: **partial (extractor timeout)** —
  a measurement datum, not a persistence failure and not a basis for any
  timeout proposal (withheld by construction on zero completed samples).

## 6. Observed fidelity gaps (no fix applied — diagnosis only)

1. Terminal ledger captured 0 events despite 2 intents and an observed
   300.1s extractor transport timeout plus an implied router success:
   `attempt_outcomes {}`, `timeouts 0`, `errors 0`, all stage timings
   `0.0`. Per-attempt outcome persistence for the success-branch
   (`failed_stage`) path did not materialize as ledger events.
2. Report states `"server_cancellation": "no timeout/cancel observed"`,
   contradicting the observed extractor timeout — `had_timeout` derives
   from the empty ledger, not from stage failure state.
3. Kill-mid-run partial fallback (exit 2/3 paths) was NOT exercised live;
   only inference-timeout durability was demonstrated. Process-kill
   durability remains test-suite-only evidence (19 offline tests).
4. Prior failed-sweep artifacts were already absent (see §2) — the
   preserve-old-artifacts rule was vacuous, not verified.
5. No accuracy, speedup, or crash-safety claim is made: single-sample
   `accuracy tp0/fp0/fn3` is recorded without interpretation; longer
   timeouts are not described as improvements.

## 7. Rollback

Nothing to roll back: no code, config, or History changes. To reproduce
or clean up: `rm -rf /tmp/calib-verify-20260916T000000-smoke`
/artifacts otherwise retained as the observation trail.

## 8. Workflows consulted vs checks actually run

- Consulted: `/ecc-verify` (project-local, this task), `/ecc-update-docs`
  (this report), `docs/reports/timeout_harness_durable_progress_2026-09-16.md`
  and `docs/reports/timeout_calibration_2026-09-16.md` (context only —
  their claims were re-verified, not assumed).
- Actually run: `ruff`, focused pytest files, full backend pytest,
  `/api/tags` + `/api/ps` + `ollama ps` + process/DB read-only probes,
  `--help` + source reads for bound verification, one bounded
  `--only` smoke run under `timeout 660s`, file read-backs above.
  Frontend build and any kill/restart/download/push: not run (out of scope).
