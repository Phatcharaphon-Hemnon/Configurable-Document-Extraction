# Timeout-calibration harness: lost-progress fix (2026-09-16)

Branch: `perf/latency-opt-20260914`. Offline implementation only — no live
inference was run, rerun, or authorized by this change.

**Problem (from `docs/reports/timeout_calibration_2026-09-16.md`):** the
60-minute sweep produced zero persisted document records and an unknown HTTP
dispatch count. That run is a failed measurement, not evidence for
production timeouts. This change fixes the harness so a killed run always
leaves an honest partial trail.

## 1. Evidence audit (artifacts preserved unchanged)

- `/tmp/calib-20260916/measurements.json` = `[]`; `calibration_report.json`
  = hand-written partial ledger (`stop_reason`: task bound reached before any
  document record was persisted; dispatch count explicitly `unknown`). Both
  files kept as-is (mtimes 12:44, after the sweep window).
- `logs/` contains no harness stdout for the run — the process was
  terminated externally at the 60-minute bound, so per-step timing evidence
  beyond the task-level statement does not exist and was not reconstructed.
- Serving implementation at audit time: harness process gone (no PID/cwd/
  entrypoint observable without restart — verification limit); only
  `ollama serve` remains. File mtimes (`calibrate_timeouts.py` 11:31,
  services 11:28) predate the artifact write (12:44), consistent with the
  reported sequence, but mtimes are inference, not proof of loaded code.
- Effective (non-secret) settings from the saved preflight:
  `ollama-local` / `qwen2.5:3b` @ `http://localhost:11434/v1`, temperature
  0.0, `EXTRACTION_MAX_TOKENS=3000`, compact OFF, regions OFF, concurrency 1.
  Production `api/.env` timeouts (read-only check): 45 / router 100 /
  extractor 150 / judge 100. Code defaults (`api/app/core/config.py`) are
  45 / 100 / 150 / 100 / OCR 120 with `EXTRACTION_MAX_TOKENS` default 8000 —
  code defaults differ from effective values; failure-time production
  timeouts are unknown unless stored with the job.
- What was active when the deadline fired is **unknown**: no per-doc record
  survived, so persisted-zero is separated from processed-zero — an empty
  measurements list is not proof of no work.

## 2. Confirmed root cause (code evidence, not speculation)

1. **End-of-run-only persistence** (`api/scripts/calibrate_timeouts.py`:
   measurements + report written once at the end of `main_async`). An
   externally killed run persists nothing, whatever it processed. Primary
   defect.
2. **Dispatch evidence discarded on timeout** (`_run_one_doc` returned
   `(rec, 0, True)`): ledger events captured before the deadline were thrown
   away and replaced with `dispatches=0`. Operator `CancelledError`
   propagated with no record at all.
3. **In-flight attempts left zero trace**: the terminal dispatch ledger only
   records attempts that reach a terminal transport outcome, so a deadline
   hitting mid-attempt was unknowable by construction.
4. **Blocking idle gate**: synchronous `time.sleep(30)` + blocking urllib
   probes inside the async sweep loop held the event loop, delaying deadline
   delivery and cancellation.
5. **Unguarded reporting**: one `git rev-parse`/serialization failure (or a
   late empty write) could erase or prevent all output; no partial fallback
   and no clobber guard existed.

## 3. Fix (harness + default-off transport support; no production change)

- `api/app/services/timeout_calibration.py` — durable-progress section:
  `atomic_write_json` (temp + fsync + `os.replace`), `ProgressStore`
  (doc-start intent persisted BEFORE processing, terminal outcome persisted
  AS SOON AS each doc finishes, phase-aware `(source_id, phase)` dedupe so
  confirmation never overwrites sweep outcomes, best-effort writes that never
  raise, clobber guards refusing empty-over-nonempty and
  thinner-over-fuller), `describe_dispatch_state` (known terminal vs
  explicitly uncertain), `build_partial_report` (completed / interrupted /
  not-attempted for every planned doc; defensive per-record; carries the
  client-timeout disclaimer), `decide_apply` (refuses `--apply` on zero /
  insufficient / unconfirmed / retry-unfit evidence), `load_progress`
  (load-only recovery, skips invalid/torn entries, never auto-resumes).
  `validate_measurement` additionally accepts the honest `interrupted`
  terminal outcome.
- `api/app/services/request_control.py` — opt-in `DispatchIntent` ledger
  (`collect_dispatch_intents`, `begin_dispatch_intent` after budget gates,
  `settle_dispatch_intent` first-settle-wins). Default `None` = zero
  production behavior change. Content-free by construction (no prompts,
  credentials, or document text).
- `api/app/services/client.py` — `_chat_with_retry` records intent before
  each transport invocation, settles it alongside the terminal dispatch
  event, and a per-attempt `finally` marks unsettled intents `uncertain`
  (covers outer-deadline `CancelledError`, which bypasses `except
  Exception`). Denied (never-sent) attempts create no intent.
- `api/scripts/calibrate_timeouts.py` — per-doc persist via the store on all
  paths; timeout path keeps captured events + unsettled intents; operator
  cancel raises `DocInterrupted` (persisted as `interrupted`, reported as
  operator-cancel, never as timeout; exit 3); async `_idle_gate`
  (`to_thread` probes with their own bound, `await asyncio.sleep` drain);
  reporting wrapped with partial-report fallback (exit 2); `--apply` gated
  by `decide_apply`; new `--only SOURCE_ID` flag for the one-document smoke
  scope (unknown ids fail fast). Experimental bounds UNCHANGED
  (300s/attempt, 600s/doc, 60min task, 60 dispatches).

## 4. Verification (real output)

- `.venv/bin/ruff check api/` → **All checks passed**.
- New `api/tests/test_timeout_calibration_progress.py` → **19 passed**
  (short test deadlines, offline doubles only): doc-deadline keeps known
  dispatches + flags uncertain; cancel is `interrupted`, never timeout; one
  completes / next hangs / deadline expires → earlier records survive with
  honest per-doc statuses; write-failure never loses memory; empty never
  overwrites evidence; garbage records still report; recovery loads valid
  partials without resuming; shared sweep+confirm caps; insufficient
  evidence refuses `--apply`; transport stall settles `timeout`; transport
  cancel leaves `uncertain`; idle gate is async and promptly cancellable;
  blocking probes are isolated off-loop; queue attribution; `interrupted`
  validates.
- Related suites (`test_timeout_calibration`, `test_dispatch_observability`,
  `test_http_budget`) → **55 passed** with the new file.
- Full backend `python -m pytest api/tests/` → **679 passed, 2 skipped**
  (pre-existing skips), same as the pre-change baseline — no regressions.
- Pre-existing LSP strictness diagnostics in `client.py` (`_json`
  possibly-unbound, capability-key widths) untouched.

## 5. One-document smoke run (PREPARED, NOT EXECUTED)

No live-inference authorization was granted; the following validates durable
measurement before any dataset sweep when authorization exists:

```bash
source .venv/bin/activate
python api/scripts/calibrate_timeouts.py \
  --out /tmp/calib-smoke --only sroie_X51005301667.jpg
# Expect: measurements.json with 1 terminal record, progress.jsonl with
# doc_start + doc_outcome, calibration_report.json; kill -9 mid-run must
# still leave progress.jsonl + partial state behind.
```

Kill-resume check: start the smoke run, `kill -9` the harness mid-document,
then inspect `/tmp/calib-smoke/progress.jsonl` + `measurements.json` — the
start intent must be present and no empty file may have replaced prior
records. Recovery (`load_progress`) is read-only; nothing auto-resumes.

## 6. Constraints honored / limits

- No deadline increases, no service restarts, no downloads, no History
  changes (writes confined to the isolated `--out` dir), no production
  configuration edits (`api/.env` byte-identical), no pushes.
- Residual uncertainty: per-document durations/splits and cold-vs-warm
  behavior of the failed run remain unmeasured; dispatch accounting counts
  confirmed terminal dispatches with uncertain in-flight counts reported
  alongside (budget checks use confirmed counts).
- Client cleanup never claims server generation stopped: every partial/full
  report after a timeout states server-side cancellation is unknown.
