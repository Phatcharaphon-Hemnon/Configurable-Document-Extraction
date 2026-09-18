# Prompt-size instrumentation at the extractor transport boundary (2026-09-17)

Observability-only, additive change. No timeout, retry, provider-call,
config, or `.env` change.

## Diff summary

- `api/app/services/request_control.py` — `DispatchEvent` gains optional
  `prompt_chars: int | None` and `prompt_sha: str | None` (default `None`;
  every other stage's contract unchanged) plus pure helper
  `prompt_fingerprint(messages)` returning `(len(canonical_json),
  sha256[:12])`. Sizes only — no message text survives.
- `api/app/services/client.py` — `_chat_with_retry` fingerprints
  `kwargs["messages"]` **only when `stage_context == "extractor"`**,
  attaches the pair to the `DispatchEvent`, and emits one extractor-only
  `LLM prompt ... prompt_chars=… prompt_sha=…` log line. Transport
  timeout, retry, and call behavior untouched.
- `api/scripts/calibrate_timeouts.py` — `_run_one_doc` records
  `extractor_prompt: {chars, sha} | None` from the first extractor event
  carrying it; `None` when no extractor attempt was recorded.
- `api/tests/test_prompt_size.py` (new, 4 tests) — fingerprint shape /
  determinism / no-body leakage, event defaults, extractor dry-run carries
  sizes, router dry-run carries `None`.

## Test results (real output)

- Focused: `test_prompt_size + timeout_calibration +
  timeout_calibration_progress + failed_stage_calibration +
  dispatch_observability + http_budget` → **73 passed**.
- Full backend `pytest api/tests/` → **715 passed, 2 skipped**
  (pre-existing skips; 164 pre-existing pydantic warnings).
- `ruff check api/` → **All checks passed** (remaining LSP diagnostics in
  `client.py` are the known pre-existing `_json`/tuple-width items).
- One test-assertion bug during the task (`in` against an `int`) fixed in
  the test, not the implementation.

## No-inference confirmation

No inference dispatch occurred: all new tests drive a real `Client` over
`httpx.MockTransport` against `https://llm.test.local/v1` (nonexistent
host, intercepted transport); no test contacts `localhost:11434` or any
real provider. No follow-up run executed.
