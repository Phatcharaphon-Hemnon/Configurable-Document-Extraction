# Single-dispatch Extractor diagnostic — sroie_X51008142033.jpg (2026-09-16)

Bounded local diagnostic authorized by `/ecc-debug`: exactly one Extractor
inference attempt via `api/scripts/diag_extractor_single.py`. No Router,
Judge, retries, format fallbacks, or corrective calls. No second attempt issued.

## Outcome

- Report status: `failed-one-dispatch` (full JSON: `/tmp/diag_extractor_single.json`).
- Dispatch count: one outbound inference HTTP attempt; per-attempt duration **45.059s**.
- Timeout source: effective **request deadline** (`LLM_REQUEST_TIMEOUT_SECONDS=45`),
  not the 150s stage or 180s overall deadline.
- Exception chain: `ClientError: LLM request timed out` → `TimeoutError` →
  `TimeoutError` → `CancelledError`.
- Returned usage: none recorded (no completed response). `finish_reason`: unknown —
  nothing returned. Schema/evidence validation: N/A (no output to validate).
- Judge explicitly unavailable; outcome is completed-cache-ineligible by construction.
- History unchanged (`data-local/extraction.db` still 7 jobs: 6 completed, 1 failed).

## Provenance gate (passed, read-only)

- Job `334f03b7-de2e-49d7-9c18-79ea243926cc`, `sroie_X51008142033.jpg`
  (`image/jpeg`, 184361 bytes; disk size matches job row).
- Source `cfbd3f60-868f-4bcb-bab5-014363617a3c`, sha256
  `4176fc77…0b3d03b`; stored OCR: 682 chars / 109 blocks, engine `tesseract`.
- No dataset annotations or box transcripts used as input. Temp catalog copy only;
  live catalog, History, and sources untouched.

## Serving implementation (observed without restart)

- API: PID 7265, `python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`,
  started 2026-09-16 03:44:10+07.
- Ollama: `ollama serve` (PID 512) + `llama-server` (PID 8995, model blob
  `sha256-5ee4…f31caa`), started 03:44:19. File mtimes predate process start —
  inference only, not proof of loaded code.
- Effective settings (numeric only): provider `ollama-local`,
  endpoint `http://localhost:11434/v1`, model `qwen2.5:3b`,
  `EXTRACTION_MAX_TOKENS=3000`, request 45s / extractor-stage 150s,
  `REGION_EXTRACTION_ENABLED=false`, `LLM_MAX_CONCURRENT_REQUESTS` unset (= default 1).
- Model state: `qwen2.5:3b` loaded (CPU, `size_vram: 0`, ctx 4096) before and after.
  `expires_at` refreshed 03:51:20 → 03:54:23, confirming the attempt reached the model.

## Failed-request trace (stored payload, job 334f03b7)

- Router 53.47s (2 attempts, 1 retry; usage 688 in / 44 out tokens).
- Extractor 92.04s (2 attempts, 1 retry, no usage tokens) ≈ 45+2+45 retry budget.
- OCR 2.09s, pipeline 145.69s. Error: `TimeoutError`, status/code null.
  Judge `unavailable`, `needs_review: true`, `acceptance_status: unevaluated`.
- Settings at failure time are **unknown** (not stored with the job); do not assume
  they equal current effective values.

## Resource observations (concurrent, not causal)

- Before: MemAvailable 968 MB; after: 897 MB. Swap used ~1.1 GB of 4 GB.
- Pre-dispatch lane check: no DB active jobs, no established TCP to
  11434/44775/8000, instant CPU 0.0% / 82.9% idle (the ~110% `ps` average was
  lifetime since 03:44 startup load, not active generation). Lane judged clear.

## Limitations (explicit)

- One timeout does not establish general speed or accuracy, nor prove memory
  pressure caused the original failure.
- Missing measurements marked unknown: token progress (no usage recorded),
  failure-time settings, per-token timing.
- Cancellation/shutdown was not observed; timeout attribution comes from the
  exception chain + 45.059s ≈ 45s request deadline, not from null status/code alone.

## Commands executed

- `python api/scripts/diag_extractor_single.py --out /tmp/diag_extractor_single.json`
  (venv active, repo root) → exit 1, `failed-one-dispatch`, 45.059s.
- Read-only checks: sqlite3 queries (mode=ro) on both DBs, `curl /api/ps`,
  `free`, `top`, `ps`, `ss`, `stat` on service files. No restarts, downloads,
  provider/model changes, History writes, or pushes.

## Files changed

None. Rollback: N/A.

## Corrections addendum (2026-09-16) — read before citing this report

1. **`expires_at` refresh is supporting server-activity evidence only.**
   The 03:51:20 → 03:54:23 refresh shows ≥1 attempt reached the model. It
   proves neither exact HTTP dispatch count nor any token-generation
   progress (no usage, no chunks were recorded).
2. **The one-attempt conclusion rests on four independent controls:**
   (a) in-process budget patched to 1 attempt / 0 timeout retries;
   (b) `guarded_create` wrapper aborting any 2nd dispatch (no
   `RuntimeError` in the chain ⇒ no 2nd dispatch attempted);
   (c) a single `extract_call` whose exception preceded every
   fallback/corrective path; (d) no Router/Judge calls in the script.
   `expires_at` corroborates (a)–(d); it does not substitute for them.
3. **`attempt_events` limitation preserved.** The run's ledger stayed empty
   because of a script bug (`ledger.extend(events)` after `try/finally`,
   skipped on exception — fixed offline afterwards, pinned by
   `test_collector_events_survive_exception`). The tier/purpose/outcome
   detail for the 45.059s run is **labeled reconstruction, not
   measurement**; historical events are unrecoverable and are not
   back-filled from offline test shapes.
4. **Timeout-implementation note (checked in `client.py`).** The 45s
   request timeout is enforced by two cooperating ~equal timers: the SDK
   client's own `timeout=45` and `asyncio.wait_for(..., timeout=45)`.
   Either may fire first; both feed the same 4-attempt retry budget
   (offline repro: SDK and wait_for paths both record + retry). It is
   therefore not a single hard 45s total — the documented 45+backoff+45
   pattern, the 150s stage guard, and the 180s overall bound sit above it.

5. **No re-run issued**: the authorization covered exactly one attempt.
   A re-run with the fixed script requires fresh authorization.
