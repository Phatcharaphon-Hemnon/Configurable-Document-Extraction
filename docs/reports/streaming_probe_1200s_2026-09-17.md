# Extended streaming probe at the 1200s ceiling (2026-09-17)

Single authorized probe: X51008142033, extractor-shaped request with
`stream=true` (diagnostic-only override, reverted), stage ceiling raised
300s -> 1200s, same setup as the 300s probe otherwise. Purpose: observe
whether content chunks eventually flow after the ~278s header delay and
capture actual total duration if it completes.

## 1. Outcome: timed-out-zero-chunks-again

- `status: dispatch-failed`, `duration_s: 1200.003` (bare `TimeoutError`,
  harness exit 1, wrapper wall 1204s).
- `established_at_s: 276.812` — response headers received ~276.8s after
  dispatch, reproducing the 300s probe's 278.2s within ~1.4s.
- `first_event_at: null`, `first_content_at: null`, `chunk_count: 0`,
  `empty_count: 0`, `assembled_chars: 0`, `finish_reason: null`,
  `parsed: false`, `diagnosis: empty` at the 1200s stage deadline.
- Exactly 1 HTTP dispatch; no retries/fallbacks/corrections (single-dispatch
  guard never triggered; no second-dispatch error recorded).
- Request parity: `prompt_chars: 3433`, `message_chars: 7382`,
  `schema_chars: 3757` — identical to the 300s probe. Same
  endpoint/model/schema/temperature/max_tokens; differences remain
  `stream=true` and no `stream_options` only.

## 2. Cumulative chunks/chars over time: flat zero for the full window

No per-chunk timeline exists because zero stream events arrived
(`first_event_at` null throughout; any arrival would have set it and
raised `chunk_count` above 0). Liveness of the observation window is
confirmed by 240 resource samples spanning 1195.6s at ~5s cadence
(MemAvailable 3285360 kB -> 960228 kB). Cumulative content: 0 chunks /
0 chars at every sampled point, including the ~923s after headers.

## 3. Discrimination (observation only, no proposal)

Two consecutive streaming attempts now show the same shape: headers at
~277s (278.2s, 276.8s), then zero content bytes until the stage deadline
(300s, 1200s). The server works ~4.6min before responding with headers
and then delivers no content chunks for the remaining ~923s of this
attempt. Whether the server is generating-but-undelivered or stuck in
prefill/grammar setup remains unknown (zero content chunks observed).
Combined with 8/8 non-streaming censored samples (7 x 300s + 1 x 1200s),
the extractor-shaped strict-schema call has never completed on this
host. One probe only — no timeout value proposed.

## 4. Bounds compliance (all met, real observations)

| Bound | Observed | Status |
|---|---|---|
| <=1200s per inference attempt | stage deadline 1200.0s; `duration_s` 1200.003 (stage guard fired) | met |
| <=1400s document/script run | overall script deadline 1400.0s; wrapper wall 1204s | met |
| <=1500s overall incl. preflight + cleanup | `timeout -s TERM 1500s` wrapper; wall 1204s, harness exit 1 | met |
| <=2 dispatches | exactly 1 HTTP dispatch (single-dispatch guard) | met |
| Stop after first timeout | single dispatch by construction; no retry/fallback/correction | met |
| Revert streaming override after | script sha256 `8b730430…` identical before/after (see §6) | met |

Pre-dispatch idleness gate (all true before dispatch): `/api/ps` empty,
no `llama-server` process, port :8000 free, History `completed 8,
failed 1` with no queued/processing, tags show `qwen2.5:3b` (no pull).

## 5. Persistence + fidelity

Full `streaming_report.json` persisted via `--out` (exit-1 path writes
before return); stdout is the same report plus a trailing newline
(50326 vs 50325 bytes, both parse). `established_at` vs `first_event`
kept distinct; zero-chunk outcome recorded as measured zeros, not
fabricated durations; no resume/second run. Sanitized: lengths/sha only
(no document text, no secrets — verified by keyword scan).

## 6. Revert confirmation

- `api/scripts/diag_extractor_stream.py` restored from pre-run backup at
  `/tmp/diag_stream_1200_backup.py`, sha256 `8b730430…` identical
  before/after (file is untracked — pre-existing status, so verified by
  sha rather than `git diff`).
- Patched constants during the run only: `STAGE_DEADLINE_S` 150 -> 1200,
  `OVERALL_DEADLINE_S` 180 -> 1400; `READ_IDLE_S` 45.0 unchanged.
- Production remains non-streaming by default (`build_chat_kwargs`
  omits `stream` unless explicitly passed; diagnosed, not modified).
- `api/.env` byte-identical before/after (sha256 `7942bfbdc924bf87…`,
  matches the prior 1200s-diagnostic value).
- No lingering probe process (pgrep clean; the single match was the
  checking shell itself). A `qwen2.5:3b` model entry remains loaded in
  `/api/ps` post-timeout with a future expiry — expected server-side
  drain, left untouched; client timeout never claims server cancellation.

## 7. Checks executed (real output)

- `ruff check api/` → **All checks passed**.
- Focused (7 files): `test_prompt_size` + `test_failed_stage_calibration` +
  `test_timeout_calibration_progress` + `test_timeout_calibration` +
  `test_dispatch_observability` + `test_http_budget` +
  `test_stage_failure_timing` → **76 passed** (50.73s).
- Full backend `python -m pytest api/tests/ -q` → **715 passed, 2 skipped**
  (107.38s; skips pre-existing; matches baseline exactly).
- Frontend build: skipped — no UI changed (per `/ecc-verify` rule).
- Diagnostic: `timeout -s TERM 1500s bash -c 'source .venv/bin/activate &&
  exec python api/scripts/diag_extractor_stream.py --out
  /tmp/streamprobe-1200s-20260917/streaming_report.json'` →
  dispatch 2026-09-17T12:25:59Z, harness exit 1, wall 1204s.

Evidence: `logs/calibration-streamprobe-1200s-20260917/` (report, stdout,
stderr). No second run, no production changes, no pushes.
