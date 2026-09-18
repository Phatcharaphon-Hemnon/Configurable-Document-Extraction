# Streaming-probe diagnostic at the 300s ceiling (2026-09-17)

Single authorized probe: X51008142033, extractor-shaped request with
`stream=true` (diagnostic-only override, reverted), 300s stage ceiling for
direct comparability with the seven 300s non-streaming censored samples.

## 1. First-byte timing: stream established at 278.2s, zero chunks before ceiling

- `established_at_s: 278.215` — response headers received (SDK
  create-return) 278.2s after dispatch.
- `first_event_at: null`, `first_content_at: null`, `chunk_count: 0`,
  `assembled_chars: 0` at the 300s stage deadline (`duration_s: 300.002`,
  bare `TimeoutError`, exit 1).
- Exactly 1 HTTP dispatch; no retries/fallbacks/corrections.
- Request-parity note (not hidden): this probe's `prompt_chars: 3433`
  counts prompt text only, vs the calibration runs' ~8.1k
  messages-canonical length — different metrics, not directly comparable.
  Same endpoint/model/schema/temperature/max_tokens; differences are
  `stream=true` and no `stream_options` (usage unavailable by design).

## 2. Discrimination: toward provider generating-but-extremely-slow

- (a) provider slow: **supported** — headers arrived, proving the request
  reached the server and the server worked it for 278s before responding
  with headers. Rules out hypothesis 3 (in-process stall before socket).
- (b) silently stuck with zero bytes: **not supported at the transport
  layer** — bytes (headers) did arrive. Whether the server was generating
  tokens or stuck in prefill/grammar setup during those 278s is still
  unknown (zero content chunks observed).
- Combined with 8/8 non-streaming censored samples, the picture is now:
  the extractor-shaped strict-schema call takes >300s server-side on this
  host (CPU inference, `size_vram: 0`), with first response headers at
  ~278s on this attempt. One probe only — no timeout value proposed.

## 3. Persistence + fidelity

Full `streaming_report.json` persisted (exit-1 path writes before return);
`established_at` vs `first_event` kept distinct; zero-chunk outcome
recorded as measured zeros (instrumentation establishes no events
arrived), not fabricated durations; no resume/second run.

## 4. Revert confirmation

- `api/scripts/diag_extractor_stream.py` restored from pre-run backup,
  sha256 `8b730430…` identical before/after; the `DIAG_STREAM_*` env knobs
  existed only in the exited process tree.
- Production remains non-streaming by default (`build_chat_kwargs`
  omits `stream` unless explicitly passed; diagnosed, not modified).
- `api/.env` unmodified; no lingering probe process (a `llama-server`
  worker remains active post-timeout — expected server-side drain, left
  untouched; client timeout never claims server cancellation).

Evidence: `logs/calibration-streamprobe-20260917/` (report, stderr,
stdout). No second run, no production changes, no pushes.
