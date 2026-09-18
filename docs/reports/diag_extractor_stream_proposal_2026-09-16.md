# Streaming extractor diagnostic — proposal and bounds (2026-09-16)

Follow-up to the single-dispatch non-streaming run
(`diag_extractor_single_2026-09-16.md`, status `failed-one-dispatch`,
45.059s request-timeout, no usage recorded). The streaming inference
itself is **not authorized by this document and was not executed**.

## Why stream (observability only)

A non-streaming timeout yields no token-progress evidence by
construction. A streaming run would provide first-token latency,
inter-token timing, and time-correlated resource sampling — the exact
measurements that separate provider-side stall from resource pressure.
Streaming does NOT accelerate generation (same server compute), and
closing the client is NOT assumed to stop server computation.

## Prepared command (DO NOT RUN without authorization)

```bash
source .venv/bin/activate
python api/scripts/diag_extractor_stream.py \
    --job-id 334f03b7-de2e-49d7-9c18-79ea243926cc \
    --out /tmp/diag_extractor_stream.json
```

## Bounds (diagnostic-only, process-local; production defaults untouched)

- Read-idle timeout: **45s** (no event, not just no content — heartbeats
  and empty chunks advance arrival stats only, never content progress).
- Generation-stage wall-clock deadline: **150s**. Generation cannot
  continue beyond it.
- Overall script deadline: **180s**, including preflight and cleanup.
- Exactly one HTTP dispatch (second-dispatch guard aborts any retry);
  no Router/Judge, no retries, no tier fallbacks, no corrective
  generation, no `stream_options` (usage stays unavailable by design).

## Controlled comparison

Reused identically: endpoint, model, prompt (same `_build_prompt` +
catalog + sanitizer + JSON suffix), schema (`ExtractionResponseSchema`),
temperature, `max_tokens`, `disable_reasoning`, provenance-verified OCR
text, temp catalog copy. Recorded differences: `stream=true`;
incremental vs whole-response delivery; idle+overall bounds instead of
the SDK per-request timeout. `response_format=json_schema` is preserved.

## Comparison limitation (explicit)

`response_format=json_schema` + `stream=true` on
ollama-local/`qwen2.5:3b` is **unverified** (no row in
`provider_capabilities.py`; mock transport only). If the endpoint
rejects the combination or streams without schema enforcement, the run
stops and reports the limitation — never silently drops the format.
`stream_options`-dependent usage stays unavailable regardless.

## Outcomes and classification

- Timestamps: dispatch, first event (= headers received), first
  non-empty content, completion — recorded separately.
- Partial JSON, interrupted streams, and `finish_reason=length` are
  **incomplete**; parsed via the existing `_try_parse_detailed`
  contract. A parsed response alone never proves extraction accuracy.
- `CancelledError` surfaces as cancellation, never timeout.
- Judge unavailable; cache-ineligible by construction. Assembled text
  is measured/classified only — never logged (a model may echo
  document text). No secrets logged.

## Verification already executed (offline, mocked transport)

- `api/tests/test_streaming_diag.py`: **11 passed** — timestamps,
  empty-chunk accounting, idle stall with partial preservation, overall
  deadline, partial classification, `length` incompleteness,
  cancellation attribution, format-rejection stop without downgrade,
  single-dispatch guard, builder default unchanged + `stream` opt-in.
- `ruff check api/`: clean. Full backend suite: **597 passed,
  2 skipped** (includes the 11 + prior 586).
- Review findings fixed: `await sampler` hang on the success path
  removed; production `client.py` untouched (only additive,
  default-off `stream` kwarg on `build_chat_kwargs`).

## Isolation and rollback

- Temp catalog copy; provenance gate (exit 2, no request) on any job /
  source / OCR mismatch; nothing written to History, sources, caches,
  or the live catalog; report to `/tmp` + stdout only.
- Rollback: delete `api/scripts/diag_extractor_stream.py`,
  `api/app/services/streaming_diag.py`,
  `api/tests/test_streaming_diag.py`; revert the `stream` kwarg in
  `provider_capabilities.py`. This document remains as the proposal
  record.

## Workflows consulted vs actually run

- Consulted: `/ecc-tdd` (RED→GREEN), `/ecc-code-review`,
  `/ecc-verify`, `/ecc-update-docs` (project templates).
- Actually run: `ruff check api/`; focused + full `pytest` with real
  output above; read-only settings/contract inspection. No live
  inference, restarts, downloads, History/source/cache changes,
  production-setting changes, or pushes.
