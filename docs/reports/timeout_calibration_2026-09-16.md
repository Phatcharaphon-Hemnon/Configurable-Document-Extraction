# Local timeout calibration — bounded SROIE sweep report (2026-09-16)

Branch: `perf/latency-opt-20260914` (rev `7c6139b`). Model/provider frozen:
`ollama-local` / `qwen2.5:3b` (no model change, per authorization answer 1).

**Headline outcome: the 60-minute task bound was reached with zero recorded
documents. No timeout proposal was derived, no confirmation ran, and
`api/.env` production timeouts are UNCHANGED** (verified: `git status` shows
no modification to `api/.env`; only `api/.env.example` carries pre-existing
branch edits). Longer timeouts are not described as a speed improvement
anywhere in this report.

## 1. Files changed and reasons (this task only)

- `api/app/services/timeout_calibration.py` — 5 new pure helpers (offline,
  mocked, temp-storage tested): `effective_attempt_ceiling` (per-attempt
  `min(request, doc-remaining, task-remaining)`), `effective_transport_timeout`
  (default-off transport application), `stage_censored_counts` (per-stage,
  never lumped), `select_confirmation_docs` (slowest + predetermined
  comparison), `derive_stage_proposal` (own-stage P95×1.25 + production
  retry-fit check + no-retry caveat), `assess_provider_idle`
  (`/api/ps`+tags alone never prove idleness; post-timeout best verdict is
  `proceed-provisional`), `plan_confirmation` (≤2 docs sharing the sweep's
  60-dispatch/60-min budget; `[]` when exhausted or sweep insufficient).
- `api/app/services/request_control.py` — one `attempt_ceiling_s`
  ContextVar (default `None` = existing behavior byte-identical).
- `api/app/services/client.py` — `_chat_with_retry` enforces
  `min(configured timeout, ceiling)` at the transport boundary (log line +
  `asyncio.wait_for`); unset ceiling changes nothing.
- `api/scripts/calibrate_timeouts.py` — full `--confirm`/`--apply`
  implementation (previously parsed-but-ignored flags): shared task budget
  across sweep+confirm, per-doc ceiling via ContextVar, per-stage censored
  summaries from ledger `ok` durations (queue wait excluded), judge-skipped
  docs counted separately (never 0s samples), `proceed-provisional` idle
  gate with extended drain + re-poll, confirm in an isolated second service
  with the proposed profile (no service restart), `--apply` requires
  same-run `--confirm` success + retry-fit on all measured stages and only
  edits local timeout keys in `api/.env` (rollback = re-apply recorded olds).
  Preflight now also pins the authorized model (`qwen2.5:3b`).
- `api/tests/test_timeout_calibration.py` — 7 new RED-first tests
  (16 total in file): ceiling arithmetic, per-stage censoring, confirmation
  selection/predetermination, stage-specific proposal + retry-fit note,
  idle semantics (`ps`-only never proves idle), transport default-off,
  confirmation budget sharing.

## 2. Evidence (corrected, with uncertainties)

Preflight (verified read-only before dispatch):
- Endpoint `http://localhost:11434/v1` reachable; tags `['qwen2.5:3b',
  'qwen2.5:1.5b']` — authorized model present, no download.
- `/api/ps` = `{"models":[]}` (cold, idle); no active application jobs
  (read-only History check, empty).
- Frozen: temperature 0.0, `EXTRACTION_MAX_TOKENS=3000`, compact OFF,
  regions OFF, concurrency 1, no retries/fallbacks/corrections.

Sweep execution (real output):
- Command: `source .venv/bin/activate &&
  python api/scripts/calibrate_timeouts.py --out /tmp/calib-20260916 --confirm`
- The run exceeded the 60-minute task bound and was terminated by the
  harness timeout with `(no output)`; `/tmp/calib-20260916/` was empty at
  inspection (records persist only at end-of-run). Dispatch count actually
  consumed is **unknown** — stated as unknown, not zero.
- Post-run `/api/ps` shows `qwen2.5:3b` resident (`size_vram: 0`, i.e.
  CPU inference on this host) with expiry 12:37+07; the `llama-server`
  worker was at ~194% CPU at inspection, so server-side generation may
  still have been in flight after our client-side bound (client timeout
  never proves server cancellation). No further inference was dispatched
  after the bound, per the stop rules.
- Machine artifacts: `/tmp/calib-20260916/measurements.json` = `[]` and
  `calibration_report.json` = run ledger with stop reason (no per-doc
  timings fabricated).

Uncertainties (explicit): per-document durations, per-stage splits,
cold-vs-warm behavior, token/server timings, and queue waits are all
**unmeasured** for this run — the end-of-run write never happened. The
only defensible timing statement is the task-level one: 10 SROIE docs did
not complete within 60 minutes under 300s-attempt/600s-doc bounds with
`qwen2.5:3b` on this host.

## 3. Confirmed defects and fixes (harness, not production)

1. `--confirm`/`--apply` were parsed but ignored (placeholder behavior).
   Fixed: fully implemented with shared budgets and same-run success gate.
2. 5-second `sleep` + `/api/ps` bool treated as idleness proof. Fixed:
   `assess_provider_idle` + extended drain + re-poll + recorded
   provisional status; unresolvable state stops the sweep.
3. Attempts ignored remaining doc/task time (fixed 300s transport
   timeout). Fixed: per-doc ceiling via ContextVar, honored at the
   transport boundary.
4. Censored counts were lumped across stages in the summary sketch.
   Fixed: per-stage attribution from recorded per-attempt outcomes.
5. `async`-vs-sync hazard reviewed: `_idle_gate` is synchronous
   (blocking probes, no concurrent tasks) — no missing `await`.
6. Stale-LSP note: the editor language server reports unknown-import
   errors for the new helpers in the test file, but `python -c` imports,
   `pytest` (16 passed), and `ruff check` all pass — LSP index staleness,
   not a code defect. Pre-existing client.py strictness diagnostics
   (`_json` possibly-unbound, capability-key tuple widths) untouched.

## 4. Exact commands/tests executed and outcomes (real output)

- `ruff check api/` → **All checks passed** (final; one F401 fixed:
  unused `stage_censored_counts` import until wired into `_summarize`).
- `python -m pytest api/tests/test_timeout_calibration.py -q` →
  **16 passed** (9 pre-existing + 7 new).
- `python -m pytest api/tests/test_timeout_calibration.py
  api/tests/test_dispatch_observability.py api/tests/test_http_budget.py -q`
  → **37 passed**.
- `python -m pytest api/tests/ -q` (full backend) → **679 passed,
  2 skipped** (pre-existing skips), 160 warnings (pre-existing pydantic
  `utcnow` deprecations).
- Preflight probes: `curl /api/tags` (2 models), `curl /api/ps`
  (empty pre-run), read-only active-job query (empty).
- Bounded sweep `--out /tmp/calib-2026-09-16 --confirm` → **time-cap**:
  terminated at the 60-minute bound, zero records, no stdout.
- Post-run probes (read-only): `ps` (no harness process; `llama-server`
  worker for `qwen2.5:3b` still active), `/api/ps` (model resident),
  `git status` (`api/.env` unmodified), `/tmp` cleanup done.
- Frontend unchanged — `npm run build` not run.

## 5. Review findings (ecc-code-review checklist, in-scope files)

- No shared mutable counters (ContextVars with token reset; ledger
  unchanged); no double-debit (transport accounting untouched).
- No cancellation misclassification (doc-deadline → `cancelled`;
  operator `CancelledError` propagates as `failed`, never `timeout`).
- No cache-eligibility change (both phases run with result cache off;
  failure semantics untouched); legacy loading untouched; partials and
  evidence fields preserved in records.
- No secrets in logs/reports (`ps` payloads excluded; names + numerics
  only); no gold/box input (post-extraction scoring only, pinned by
  existing tests); sanitizer path unchanged.
- No provider/model/budget/validation/concurrency/regions-default change
  (preflight refuses non-`ollama-local`, non-`qwen2.5:3b`, regions-on).
- No runtime-storage writes (isolated `/tmp` only; live DB opened `ro`).
- Decision: **pass** with the limitations below.

## 6. Proposal / confirmation / apply status

- Proposal: **none derived** (zero completed samples; P95 withheld by
  construction). No Router/Judge inference from Extractor timings was
  made — there is nothing to inflate from.
- Confirmation: **blocked** (`plan_confirmation` requires ≥2 completed;
  sweep produced 0). No confirmation docs ran.
- Apply: **not performed** (`api/.env` byte-identical; `--apply` was not
  passed and its preconditions were unmet). Per order
  measure→propose→confirm→apply, there is nothing to apply.
- Retry-policy note (unvalidated either way): production keeps a
  same-tier timeout retry needing ~2×request+2s inside each stage limit;
  the no-retry benchmark cannot validate retry timing — recorded in
  `derive_stage_proposal` for any future run.

## 7. Remaining limitations and next steps (no automatic extension)

- The bottleneck is unresolved: whether `qwen2.5:3b` CPU inference
  (context 4096, `size_vram: 0`) simply exceeds 300s/attempt on SROIE
  full-page extraction, or a smaller subset would complete, is unknown
  because per-doc records never persisted. A future authorized run could
  (a) stream per-doc records incrementally instead of end-of-run only,
  and/or (b) narrow scope (fewer docs) within a fresh 60-min/dispat
...[truncated 1583 chars]