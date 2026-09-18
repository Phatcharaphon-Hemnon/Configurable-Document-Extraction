# Router reasoning-budget exhaustion on dense forms (2026-09-18)

## Symptom

`funsd_91361993.png` (dense FUNSD form) failed at Router with a technical
failure, while receipts on the same model passed:

```
Router failed: LLM output failed (empty) for schema RoutingResponseSchema
(model=gpt-oss:20b, format=json_schema, max_tokens=400,
finish_reason='length', len=0, tokens=688/400/1088, model_calls=2)
```

## Root cause

`gpt-oss:20b` is a reasoning model whose thinking trace cannot be disabled
(Ollama exposes only low/medium/high, and the client sends no reasoning
level for this pair). On dense form text the trace filled the entire
`ROUTER_MAX_TOKENS=400` output budget before any JSON was emitted:
`message.content` came back empty with `finish_reason='length'`. The
corrective retry then burned a second identical 400-token call that was
guaranteed to fail (`model_calls=2`) — same signature as the earlier
`qwen3:8b` Router case, different model.

A second gap: the previous empty+length fix only covered "empty *after
stripping wrappers*". A literally-empty `content` field took a separate
early-return path that never consulted `finish_reason`, so it reported
`empty` (retry allowed) instead of `truncated` (fail fast).

## Changes

- `api/.env` + `api/.env.example`: `ROUTER_MAX_TOKENS=400 → 1500`.
  Typical Router JSON is ~50–200 tokens; 1500 leaves headroom for the
  thinking trace. No code change needed for the blocker itself.
- `api/app/services/client.py`: new `_empty_diagnosis()` helper — empty
  output with `finish_reason` in (`length`, `max_tokens`, `truncated`) is
  reported as confirmed `truncated`, which skips the doomed same-budget
  corrective retry. Both `_classify_parse_failure` empty paths and both
  `_try_parse_detailed` early returns delegate to it. Genuinely blank
  answers (`finish=stop`, no metadata) stay `empty` with retry allowed.
- `docs/tech-stack.md`: token-budget line updated.

## Verification

- `test_empty_with_length_finish_is_confirmed_truncation` (new):
  empty+length → truncated via classify and try_parse; empty+stop and
  metadata-free empty stay empty.
- `ruff` clean; classification suites
  (`test_classified_recovery`, `test_client_fallback_tiers`,
  `test_think_stripping`) green (24 passed).
- Live rerun of the failing FUNSD page (`ollama-cloud/gpt-oss:20b`,
  budget 1500): Router returned valid JSON in **1 attempt (466 output
  tokens)** — no technical failure. The page routed `invoice` with
  `completeness=0.5` and honest `needs_review` flags (missing required
  fields), which is the designed outcome for an ambiguous form, not a
  crash. (A clean `unsupported` short-circuit remains an option if the
  Router prompt is later tuned to reject forms outright.)

## Follow-up (optional, not done)

Add `REASONING_EFFORT_ALLOWLIST` row for
(`https://ollama.com/v1`, `openai/gpt-oss-20b`) with low/medium/high after
a live probe confirms acceptance — would cut per-call reasoning burn per
the token-minimization rule.
