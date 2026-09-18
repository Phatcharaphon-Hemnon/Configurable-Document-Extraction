# Streaming extractor diagnostic — sroie_X51008142033.jpg (2026-09-16)

Bounded local diagnostic authorized by `/ecc-debug`: exactly one streaming
Extractor inference dispatch via `api/scripts/diag_extractor_stream.py`.
No Router, Judge, retries, format downgrade, or corrective calls.
No second request issued after the outcome.

## Outcome

- Report status: `dispatch-failed` (full JSON: `/tmp/diag_extractor_stream.json`).
- Dispatch count: **one** outbound inference HTTP attempt. The
  `single_dispatch_guard` raises `RuntimeError` on any 2nd call; no such
  error appears, so exactly one dispatch was attempted.
- Duration: **150.002s** — the outer generation-stage deadline, not a
  token-progress measurement.
- Timeout source: outer **150s stage guard** (`asyncio.timeout(150)` around
  `create(stream=True)` + stream collection). The initial streaming
  `create` never returned headers within 150s, so the 45s read-idle bound
  never engaged (it governs per-event waits on an established stream).
  The SDK-level timeout (180s on this diagnostic path) did not fire.
- Exception: bare `TimeoutError` (empty message — the `asyncio.timeout`
  form, not `StreamIdleTimeout`/`StreamOverallTimeout`; transport never
  established, so neither stream classification engaged).
- Time to headers: **never** (no headers within 150s). First event: none.
  First non-empty content: none. Completion: no.
- Empty-event count: 0. Assembled chars: 0, chunks: 0.
- Usage: unavailable (no `stream_options` by design). `finish_reason`:
  null — nothing returned.
- Schema/evidence validation: N/A (no output to validate).
- Judge explicitly unavailable; outcome cache-ineligible by construction.
- History unchanged (`data-local/extraction.db` still 7 jobs: 6 completed,
  1 failed). No downloads, production-setting changes, or pushes.

## Timeout/report wording correction (done before dispatch)

1. **Non-streaming path** (`api/app/services/client.py:882-884`):
   `asyncio.wait_for(self._client.chat.completions.create(**kwargs),
   timeout=self._timeout)` encloses exactly **one**
   `chat.completions.create` coroutine = one whole-response HTTP dispatch
   attempt. `self._timeout` is the effective `LLM_REQUEST_TIMEOUT_SECONDS`
   (45s; `client.py:730`). The SDK client is separately constructed with
   `timeout=45` (`client.py:745-759`). Either 45s timer may fire first;
   both feed the same retry budget. `wait_for(45)` never encloses the
   retry loop, the stage guard, or the overall run.
2. **Streaming path, this run** (`api/app/services/streaming_diag.py:133-134`):
   `asyncio.wait_for(iterator.__anext__(), timeout=45)` encloses exactly
   **one next-stream-event wait** (any SSE chunk, including empty /
   heartbeat chunks — heartbeats advance arrival stats only, never
   content). It does not enclose the generation, the dispatch, or the run.
   Whole-run bounds for this diagnostic: 150s stage (`asyncio.timeout`),
   180s overall script deadline.
3. **Historical text disposition:** the only prior wording on this point —
   item 4 of the Corrections addendum in
   `docs/reports/diag_extractor_single_2026-09-16.md:100-106` ("two
   cooperating ~equal timers … Either may fire first") — was re-checked
   against `client.py:730/748/759/882-884` and is **accurate; no text
   removed or amended**. Original measured evidence from that run
   (45.059s, exception chain, `attempt_events` reconstruction caveat,
   `expires_at` corroboration-only status) is preserved as-is.

## Preflight (passed, read-only; no restarts, unloads, or kills)

- Provenance gate: job `334f03b7…`, `sroie_X51008142033.jpg`
  (`image/jpeg`, 184361 bytes; disk size matches job row); source
  `cfbd3f60-…`; stored OCR 682 chars / 109 blocks, engine `tesseract`.
- Local endpoint `http://localhost:11434/v1`, model `qwen2.5:3b`.
- Effective settings (numeric only): `EXTRACTION_MAX_TOKENS=3000`,
  request 45s / extractor-stage 150s, `REGION_EXTRACTION_ENABLED=false`,
  temperature 0.0, prompt 3433 chars, schema 3757 chars.
- Lane check: no active jobs (all 7 terminal), no established TCP to
  11434/8000, CPU ~76.7% idle. Lane judged clear.
- Model state before: reachable, **no model loaded** (`/api/ps` empty).
  Memory before: MemAvailable ~2.7 GB, swap used ~1.1 GB of 4 GB.
- Isolated storage: `/tmp` writable (3.8 GB avail); report to
  `/tmp/diag_extractor_stream.json` + stdout only.

## Resource observations (concurrent, not causal)

- Model state after: `qwen2.5:3b` loaded (CPU, `size_vram: 0`, ctx 4096,
  2.16 GB). The dispatch reached the server and triggered a cold model
  load — server-activity evidence only. Per standing rules, no token
  progress is inferred from this, and the client-side close is not assumed
  to have stopped server computation (server may still be generating;
  no follow-up request issued to check).
- Memory: MemAvailable fell 2744 MB → 854 MB across 30 samples (5s
  cadence); swap free fell ~448 MB. Concurrent observation only — does
  not by itself separate cold-load cost from generation stall.
- Comparison limitation: the non-streaming attempt failed at 45.059s on
  the request deadline with a warm model; this streaming attempt never got
  headers within 150s with a cold model. Different failure points under
  different start states — not a like-for-like speed comparison. The one
  new measured fact: **time-to-first-stream-event (headers) exceeded 150s
  from a cold start**, which the non-streaming form could not have
  separated from mid-generation stall.

## Unresolved hypotheses (explicit)

- Provider-side stall vs resource pressure (cold 2.16 GB load into
  ~2.7 GB available + swap) as the cause of the >150s header wait —
  needs a warm-model streaming run under fresh authorization to separate.
- Whether the server continued generating after the client-side close is
  unknown (no post-hoc probing performed; would require a new request).

## Commands executed

- Read-only preflight: sqlite3 queries (`mode=ro`), `curl /api/ps`,
  `free`, `top`, `ss`, provenance snippet via `verify_provenance`.
- `python api/scripts/diag_extractor_stream.py --job-id
  334f03b7-de2e-49d7-9c18-79ea243926cc --out
  /tmp/diag_extractor_stream.json` (venv active, repo root) →
  `dispatch-failed`, 150.002s, `TimeoutError`.
- Post-run read-only: `curl /api/ps`, job-count query. No restarts,
  downloads, History writes, production-setting changes, or pushes.

## Corrections addendum (2026-09-16 offline audit — no new dispatch)

Read before citing the Outcome section above. Reconciled evidence:
stored `/tmp/diag_extractor_stream.json`, `data-local/extraction.db`
(active: 6 completed + 1 failed = 7 jobs; `data/extraction.db` is stale
Sep-15 and was not used), `api/.env` effective values (numeric only),
code, and Ollama server logs (`journalctl -u ollama`, read-only).

### 1. Response-header observability — prior wording was unsupported

- No header timestamp exists anywhere on this path. `StreamTimestamps`
  set `first_event_at` to collect-entry time, not to the `create` return;
  on the failure path timestamps were `null` entirely. The module
  docstring claim ("first_event_at … i.e. response headers received")
  was wrong and is fixed offline (see Files below): the field now
  records the first observed stream event, and a caller-supplied
  `established_at` records the SDK `create`-return moment, explicitly
  labeled SDK stream-establishment time — never a header timestamp
  (the SDK exposes no header hook; unknown stays `None`).
- "Zero events proves zero headers" is retracted as stated. The valid
  argument is narrower: had the stream established before ~105s into
  the stage, the 45s read-idle timer would have raised
  `StreamIdleTimeout`; it did not, and the bare outer `TimeoutError`
  fired at 150.002s. So client evidence excludes establishment before
  ~105s — establishment inside the final ~45s window (outer fires
  before idle can) remains unexcluded from client evidence alone.
  Server evidence (below) is consistent with no first token before
  cancel but likewise timestamps no headers. Header timing for this
  run is therefore `unknown`, not "never".

### 2. Timeout coverage — corrected table

- SDK-level timeout EXISTS on this path: `AsyncOpenAI(timeout=180)`
  (`diag_extractor_stream.py`), i.e. httpx connect/read/write/pool all
  180s. The report's `request_differences` line ("no SDK-level
  per-request timeout") was false and is fixed to state the 180s SDK
  timeout (longer than the stage, so the stage fires first — it did
  not fire here). Effective HTTP client read timeout: 180s (confirmed
  against installed `openai` 2.46.0 / `httpx` 0.28.1 semantics).
- The 45s `__anext__` timer covers exactly one per-event wait on an
  established stream; it never covered connection, header wait, or
  stream establishment.
- Dead bound found and fixed: the inner `overall_deadline=150s`
  (measured from collect-entry, i.e. post-establishment) could never
  fire before the outer 150s stage measured from pre-dispatch, so
  `StreamOverallTimeout` was unreachable on this path. The script now
  passes the REMAINDER of the stage budget, restoring
  create-stall vs mid-stream-stall attribution.

### 3. Cold-start evidence — now quantified, conservative reading holds

Server log (times +07:00): dispatch triggered cold load 04:11:49;
model loaded 04:11:53 (**~4s load** — matches the 5s client memory
drop 2744→1076 MB); prompt `task.n_tokens = 2084` (fits ctx 4096, no
context warning, no allocation failure, `--context-shift` armed);
prompt eval at ~28 tok/s (2048 tokens by 04:13:07, t=73.5s);
`POST /v1/chat/completions → 200 in 2m29s` (client-side close at the
stage deadline, not a server error); `slot release … n_tokens = 2650,
truncated = 0` (server was mid-generation at cancel); slots idle
after. So of the 150s: ~4s load / ~74s prompt eval / ~72s
generation-in-progress (~566 further tokens). Cold start is a ~4s
footnote, and the prior warm-model 45.059s request-timeout failure
(`diag_extractor_single`) independently excludes cold start as the
general explanation.

### 4. Confirmed cause (both runs, no new run needed)

CPU throughput vs deadlines, not a transient stall: this prompt is
2084 tokens and prompt eval alone (~74s at ~28 tok/s) deterministically
exceeds the 45s request timeout — the warm 45s failure class can never
succeed on this hardware at these settings. The 150s streaming budget
covers load+eval (~78s) but not eval plus generation (~8 tok/s)
toward the 3000-token budget. Latent mismatch flagged (not the
cause): `EXTRACTION_MAX_TOKENS=3000` exceeds ctx headroom
(4096−2084=2012); `truncated=0` at cancel, `--context-shift` would
shift rather than error. Missing measurements preserved as unknown:
header arrival, per-token client timing, failure-time historical
settings. No token progress is inferred from CPU/memory; usage stays
"unavailable (no stream_options by design)".

### 5. Next single experiment (requires fresh authorization; not run)

If authorized: one warm-model streaming run with the fixed
instrumentation (establishment timestamp + remainder overall budget).
Fast establishment + slow first token ⇒ generation-bound (expected;
directs prompt-size/output-budget/deadline work). Slow establishment
⇒ transport/scheduling stall (directs connection/server-queue work).
Either outcome is interpretable only because the fix separates the
two clocks — the current 150.002s figure cannot separate them.

## Files changed (this audit; offline only — no dispatch, restart, or kill)

- `api/app/services/streaming_diag.py` (timestamp semantics +
  `established_at` + `remaining_overall` helper).
- `api/scripts/diag_extractor_stream.py` (establishment timestamp,
  remainder overall budget, SDK-timeout wording correction).
- `api/tests/test_streaming_diag.py` (+3 tests: establishment
  passthrough/unknown, empty-stream nulls, remainder helper).
- This report (this addendum; original sections preserved as-is).
- Verify: `ruff check api/` clean; `pytest api/tests/` **600 passed,
  2 skipped** (was 597 + 11-streaming incl.; +3 new).
- Rollback: `git diff -- api/app/services/streaming_diag.py
  api/scripts/diag_extractor_stream.py api/tests/test_streaming_diag.py`
  then checkout; delete nothing else. No History, sources, caches,
  settings, or pushes touched.

## Workflows consulted vs actually run

- Consulted: `/ecc-debug` (authorization + bounds), `/ecc-update-docs`
  (this report's required contents).
- Actually run: read-only preflight checks listed above; the single
  authorized streaming dispatch; post-run read-only state checks.
- Not run: Router/Judge, retries, second dispatch, frontend build,
  full pytest suite (no code changed), any live inference beyond the one
  authorized dispatch.
