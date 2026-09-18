# Qwen3 thinking-disable on ollama-local — probe + fix (2026-09-18)

## Symptom

Router on `ollama-local / qwen3:8b` failed technically on receipt
`X51008099088.jpg` (Restoran Wan Sheng, Tax Invoice 1213543):

> `LLM output failed (empty) for RoutingResponseSchema (max_tokens=400,
> finish_reason='length', len=0, tokens=619/400/1019, model_calls=2)`

`UNCLASSIFIED / failed at Router` — Extractor/Validator/Judge never ran.

## Root cause

`qwen3:8b` is a thinking model. The client sent
`extra_body={"reasoning":{"enabled":False}}`, which Ollama's
`/v1/chat/completions` **ignores** — the thinking trace burned the whole
400-token Router budget, the call hit `length`, and after think-stripping
nothing remained (`len=0`). The single corrective retry repeated the same
shape and failed again (`model_calls=2`).

## Probe (live, `http://100.123.255.72:11434`, Ollama 0.34.0, read-only)

Same Router-style prompt + strict `json_schema`, `max_tokens=400`:

| Variant | Time | Completion | Result |
|---|---|---|---|
| A. baseline `extra_body` | 57.0s | 392 tok | valid JSON, barely fit |
| B. `reasoning_effort="none"` | 8.7s | 55 tok | valid JSON, thinking off |
| C. `reasoning={"effort":"none"}` | 6.0s | 53 tok | valid JSON, thinking off |
| D. `/no_think` prompt suffix | 47.2s | 389 tok | still thinking — **ineffective** |

Decision: send top-level `reasoning_effort="none"` (variant B, the
documented OpenAI-style field); do NOT use the `/no_think` suffix.

## Fix (`api/app/services/client.py`, all stages via the shared client)

- `is_qwen_thinking_model()` — matches `qwen3*` / `qwq*` (basename after `/`).
- `_resolve_reasoning_effort()` — auto-returns `"none"` for Qwen thinking
  models on provider `ollama-local` (unless explicitly rejected before, in
  which case the existing unsupported-parameter fallback drops it and
  retries the same tier — safe on servers that refuse `none`).
- `_resolve_temperature(..., model=)` — Qwen thinking models on
  ollama-local default to `0.7` (Qwen non-thinking guidance) instead of
  `0.0`; explicit per-call values still win. All agents (Router/Extractor/
  Judge) pass no temperature, so all three are covered with no agent edits.
- `_classify_parse_failure()` — empty-after-strip with
  `finish_reason in (length, max_tokens, truncated)` is now `truncated`
  (was `empty`), so a thinking-burned budget is reported honestly instead
  of triggering a doomed same-budget retry.
- `ROUTER_MAX_TOKENS=400` unchanged — 55 tokens suffice once thinking is off.

## Verification

- `api/tests/test_qwen_thinking_off.py` (7 tests): kwargs (`none`, no
  `extra_body`, temp 0.7), non-Qwen unchanged, explicit temp wins,
  non-ollama Qwen unchanged, empty+length→truncated, empty+stop→empty.
- `ruff check api/` clean; related suites (reasoning_effort, fallback
  tiers, classified_recovery, provider_compat, think_stripping) green.
  Full-suite failures/collection errors are byte-identical before/after
  (pre-existing stale branch state, unrelated).
- Live e2e on the failing receipt text with real `qwen3:8b`: Router
  18.5s/49tok/1 attempt → `invoice`; Extractor 8 fields + 1 table;
  `failed_stage=None`. `needs_review=True` for honest data reasons
  (semantic evidence, missing `seller_name`), not technical failure.
