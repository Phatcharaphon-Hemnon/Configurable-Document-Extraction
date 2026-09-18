# Strict-mode falsification smoke (2026-09-17): still censored at 300s

Final falsification in the staged chain: disable `strict:true` for the
extractor stage only (temporary, this-run-only override), same document
(X51008142033), same 300s ceiling — testing whether strict-mode /
constrained-decoding itself, independent of schema shape, is the stall's
cause, now that both value-union narrowing and tables-nesting removal were
falsified as insufficient.

## 1. Override mechanism (extractor-only, process-local, reverted)

- Runner `/tmp/strict_probe_run.py` (outside the repo; no repo file touched):
  monkeypatched `Client._strongest_tier` to return `json_object` only when
  `stage_context == "extractor"` (set by `TimeoutGuard.track("extractor")`).
  Router/judge fell through to the original path
  (`disable_strict_json_schema=False` → strict `json_schema` tier).
- `json_object` sends `response_format={"type": "json_object"}` with NO schema
  (`client.py:2052-2088`); the strict tier sends `strict:true` + full schema
  (`client.py:2005-2026`). So this probe removed constrained-decoding
  (grammar) from the extractor call while keeping prompt text, schema for
  client-side parsing, single-attempt/no-fallback harness, and non-streaming
  production shape identical.
- Production shape otherwise byte-identical to the narrowed-schema smoke:
  `extractor_prompt` 8021 chars, sha `3798b65d77e7` (full schema with tables,
  narrowed value union retained; `EXTRACTION_TABLES_ENABLED` untouched/ON).
- Override fired exactly once (one `STRICT-PROBE override active` stderr line,
  extractor `qwen2.5:3b`); restored in `finally` + died with the process
  (`STRICT-PROBE override reverted` last stderr line).

## 2. Live outcome: STILL CENSORED at 300s

One document execution, X51008142033, non-streaming, single attempt each:

- Router ok: transport 91.31s / stage-block 91.32s (strict `json_schema` tier,
  default path — unaffected by the override).
- **Extractor timeout at 300.10s** transport / 300.12s stage-block
  (attempt 1/1, `json_object` tier — strict:true bypassed), outcome partial,
  `validation_errors: ["Extractor failed: LLM request timed out"]`,
  judge `unavailable` (`0.0`/`not_run` — the only valid zero), 0 fields.
- 2 dispatches (`dispatches_known 2`, `dispatch_intents 2`,
  `terminal_by_outcome {ok: 1, timeout: 1}`), `retries 0, fallbacks 0,
  corrections 0`; `confirm []`; `applied {false, "not requested"}`.
- Doc `total_seconds` 396.40 (ocr 3.74 + router 91.32 + extractor 300.12 +
  ~1.2s overhead); harness `task_elapsed_s` 396.41. Within all bounds
  (300/400/500). Outer `timeout 500s` wrapper exited 0.
- No second run, no confirm/apply, no restarts beyond this run, no pushes.

## 3. Falsification reading

Per the chain's own logic, completing would have implicated `strict:true` /
constrained-decoding itself as the root cause. Staying censored with the
grammar entirely removed **exonerates strict-mode as the stall mechanism**
(alongside the already-falsified value-union narrowing and tables nesting).

The cause is therefore something else entirely — host resource contention
(CPU inference, `size_vram: 0`; post-run model resident on 100% CPU),
provider/model behavior independent of request shape, or something not yet
isolated. The next step per the task order is direct host-level observation
(CPU/mem/process state) during an in-flight call rather than further
request-shape variants.

No production strict-mode change is proposed from this single probe: the
earlier feasibility findings (Validator handles unvalidated output; cost is
operational — more parse-fallback cycles) remain unevaluated tradeoffs for a
separate decision, not conclusions of this run.

## 4. Persistence + measurement-fidelity outcome: PASS

- `progress.jsonl` has 2 lines: `doc_start` persisted BEFORE inference, then
  `doc_outcome`. Full `calibration_report.json` written on the exit-0 path
  (`task_elapsed_s` 396.41, `store_write_errors []`).
- Dispatch events survive: `dispatches 2 / dispatches_known 2 /
  dispatch_intents 2 / terminal_by_outcome {ok: 1, timeout: 1}`.
- Completed vs censored kept separate: `attempt_durations.router [91.31]`,
  `censored_durations.extractor [300.10]` (never merged); extractor/judge p95
  withheld (`n_censored 1` / `no completed samples`); router p95 from n=1
  flagged `provisional_tail: true`, candidate 114.14 reported but NOT adopted
  (`confirm_ok false`, nothing applied).
- `server_cancellation` honestly reads "unknown — client timeout never proves
  server-side cancellation …". Post-run `/api/ps` shows `qwen2.5:3b` resident
  (CPU, context 4096, ~2 min expiry) — expected post-run residency, not a
  discrepancy. No lingering probe process. No discrepancies found.

## 5. Revert confirmation (production remains strict:true by default)

- Tier override was process-local only: explicit restore in `finally` + process
  exit; `grep STRICT-PROBE api/app/services/client.py` finds nothing.
- `api/.env` byte-identical before/after
  (`sha256 7942bfbdc924bf87…`, neither `DISABLE_STRICT_JSON_SCHEMA` nor
  `EXTRACTION_TABLES_ENABLED` present — defaults active).
- Source hashes identical before/after for `client.py`, `config.py`,
  `extractors.py`, `llm_schemas.py` (pre-existing working-tree diff from prior
  falsification stages unchanged; zero additional diff from this probe).
- Fresh-process `Settings()`: `disable_strict_json_schema=False`,
  `extraction_tables_enabled=True` — strict:true remains the default for all
  stages.

Evidence: `logs/calib-strictmode-20260917/` (stdout, stderr, measurements,
progress, report). Isolated out dir: `/tmp/calib-strictmode-20260917/`.
Runner (not in repo): `/tmp/strict_probe_run.py`.
