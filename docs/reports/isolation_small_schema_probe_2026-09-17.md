# Isolation probe: extractor prompt + router small schema, streaming (2026-09-17)

Single authorized probe: X51008142033 (`sroie_X51008142033.jpg` /
job `334f03b7-de2e-49d7-9c18-79ea243926cc`), extractor-length prompt base
paired with the router's small strict-schema (`RoutingResponseSchema`)
streaming, one dispatch. Purpose: isolate whether the ~277s stall +
subsequent silence is caused by schema size or prompt length.

## 1. Outcome: stream-completed — headers 158.3s, first content +21ms, 21 chunks

- `status: stream-completed`, `duration_s: 163.082` (harness exit 0,
  wrapper wall ~167s: 2026-09-17T13:11:32Z → 13:14:19Z).
- `established_at_s: 158.3` — response headers ~158s after dispatch
  (SDK create-return; header hook unavailable, same caveat as prior probes).
- `first_event_at_s: 158.321`, `first_content_at_s: 158.321` — first
  stream event AND first content ~21ms after headers. No post-header stall.
- `chunk_count: 21`, `empty_count: 1`, `assembled_chars: 69`,
  `finish_reason: stop`, `parsed: true`, `diagnosis: ok`
  (parsed under `RoutingResponseSchema`; parse validity alone never
  proves extraction accuracy — content was the 69-char router-shaped
  JSON for the extractor prompt, expected shape mismatch, not evaluated).
- Inter-arrival: 21 gaps, `max_s: 0.254` — steady flow once headers arrived.
- Exactly 1 HTTP dispatch; no retries/fallbacks/corrections (single-dispatch
  guard never triggered; no second-dispatch error recorded).

## 2. Direct comparison against the two prior large-schema probes

| Probe | Schema (wire) | Prompt base | Message | Headers | First content | Chunks | Outcome |
|---|---|---|---|---|---|---|---|
| 300s probe | Extraction large (3757) | 3433 | 7382 | 278.2s | none @300s | 0 | timeout, exit 1 |
| 1200s probe | Extraction large (3757) | 3433 | 7382 | 276.8s | none @1200s (923s post-header silence) | 0 | timeout, exit 1 |
| **This probe** | **Routing small (620)** | **3433 (identical)** | **4181** | **158.3s** | **158.3s (+21ms)** | **21 / 69 chars / stop / parsed** | **completed 163.1s, exit 0** |

Request parity (not hidden): same endpoint (`http://localhost:11434/v1`),
model (`qwen2.5:3b`), temperature (0.0), max_tokens (3000),
disable_reasoning, compact=false, stream=true, no stream_options.
Prompt base byte-identical to the production extractor prompt
(`_build_prompt("invoice", compact catalog, stored OCR text, None)` —
3433 chars in all three probes). The ONLY deliberate difference is the
schema: prompt-text suffix + wire `response_format` both
`RoutingResponseSchema` (620 chars) instead of `ExtractionResponseSchema`
(3757 chars). Message shrank 7382 → 4181 chars (−3201).

## 3. Interpretation (observation only, no proposal)

Headers/chunks arrived fast relative to both priors: headers ~120s
earlier (158.3s vs 278.2s / 276.8s), and content flowed immediately
(21ms header→first-content, max gap 0.25s) versus 923s of post-header
silence in the 1200s probe. Since the prompt base is byte-identical,
the difference isolates to **schema size, not prompt length**: the
large strict-schema call stalls (~277s headers + zero content), the
same prompt with the small strict-schema completes. Single probe —
**no production fix or timeout value is proposed from this run**.

## 4. Bounds compliance (all met, real observations)

| Bound | Observed | Status |
|---|---|---|
| <=300s per inference attempt | stage deadline 300s; completed at 163.082s, no timeout fired | met |
| <=400s document/script run | overall script deadline 400s; wrapper wall ~167s | met |
| <=500s overall incl. preflight + cleanup | `timeout -s TERM 500s` wrapper; wall ~167s, harness exit 0 | met |
| <=2 dispatches | exactly 1 HTTP dispatch (single-dispatch guard) | met |
| Stop after first timeout | no timeout occurred; single dispatch by construction, no retry/fallback/correction | met |
| Revert temp construction after | `/tmp`-only probe script; production hashes identical before/after (see §6) | met |

Pre-dispatch idleness gate (all true before dispatch): `/api/ps`
`{"models":[]}` (empty), no `llama-server` process, port :8000 free
(only :11434 listening), History `data-local` completed 8 / failed 1
with no queued/processing, tags show `qwen2.5:3b` + `qwen2.5:1.5b`
(no pull). Note: one harness attempt crashed at import
(`IndexError` in `REPO_ROOT` parents — `/tmp` script depth) BEFORE
any dispatch; zero HTTP sent, budget intact; fixed path and re-ran.
Exactly ONE document execution dispatched in total.

## 5. Persistence + fidelity

Full `streaming_report.json` persisted via `--out` (exit-0 path) at
`/tmp/iso_small_schema_20260917/streaming_report.json`, copied to
`logs/iso-small-schema-20260917/` with stdout/stderr.
`established_at` (outer dispatch clock) vs `first_event`/`first_content`
(inner collect clock, reported as outer-relative offsets) kept distinct;
21-chunk outcome recorded as measured counts (no invented durations);
no resume/second run. Sanitized: lengths/sha only (no document text,
no secrets). Fidelity note: raw `timestamps.dispatched_at` is the inner
collect-start clock (≈ the established moment by construction), while
`established_at_s`/`first_event_at_s`/`first_content_at_s` are all
relative to outer dispatch — same separation convention as prior probes.

## 6. Revert confirmation

- Temporary construction lived ONLY in `/tmp/iso_small_schema_probe.py`
  (never in `api/`); no production schema wiring touched.
- `sha256sum -c` post-run: `diag_extractor_stream.py`
  (`8b730430…`), `extractors.py`, `llm_schemas.py`, `client.py`,
  `api/.env` (`7942bfbd…`) — all OK, identical before/after.
- Production remains non-streaming by default (`build_chat_kwargs`
  omits `stream` unless explicitly passed; diagnosed, not modified).
- No History/sources/cache writes: DB counts unchanged
  (`data-local` 8 completed / 1 failed; `data` 15 completed / 1 failed).
- No lingering probe process (pgrep clean — sole match was the checking
  shell). A `qwen2.5:3b` model entry remains loaded in `/api/ps`
  post-run with a future expiry — expected server-side state after a
  completed call, left untouched.

## 7. Checks executed (real output)

- `ruff check api/` → **All checks passed**.
- Full backend `python -m pytest api/tests/ -q` → **715 passed, 2 skipped**
  (107.74s; matches baseline exactly).
- Frontend build: skipped — no UI changed (per `/ecc-verify` rule).
- Diagnostic: `timeout -s TERM 500s bash -c 'source .venv/bin/activate &&
  exec python /tmp/iso_small_schema_probe.py --out
  /tmp/iso_small_schema_20260917/streaming_report.json'` →
  dispatch 2026-09-17T13:11:32Z, harness exit 0, wall ~167s.

Evidence: `logs/iso-small-schema-20260917/` (report, stdout, stderr).
No second run, no production changes, no pushes. No fix or timeout
proposed — single probe only.
