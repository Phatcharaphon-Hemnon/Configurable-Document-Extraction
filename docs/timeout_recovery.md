# LLM Timeout Recovery & Latency Tuning

> Last updated: 2026-09-04. Background: production extractions hit 90s
> schema-call timeouts with empty responses (Router 90s+ → Extractor 109s →
> ~6 min per document).

## Root cause (measured, not guessed)

`api/scripts/time_gateway_modes.py` times strict-`json_schema` vs
`json_object` vs plain calls against the live gateway:

| Mode | Result |
|------|--------|
| strict `json_schema` | ✅ ~3–5s, parses (even with a 23k-token prompt) |
| `json_object` | ❌ fast but wrong shape (`classification` vs `doc_type`) — never parses |
| plain | ❌ fast prose — never parses |

Conclusion: the hangs were **transient gateway stalls**, not a strict-mode
incompatibility. Flipping `DISABLE_STRICT_JSON_SCHEMA=true` would break every
extraction (nothing downstream of strict parses). Strict stays ON; stalls are
handled by retry (below).

## How recovery works now

See [LLM request queue](llm_request_queue.md) for the current shared concurrency
limit and four-attempt generation budget. Exhausted timeouts/rate limits never
trigger an output-format fallback. Queue waiting occurs before stage timers.

1. **Same-tier retry** (`client.py::_chat_with_retry`): a stalled
   call is retried once on the SAME tier after
   `TIMEOUT_RETRY_BACKOFF_SECONDS` (2s). A stall says nothing about the
   request shape, so tier-degradation only happens for genuinely
   unparseable responses. Verified live: a 45s stall retried and completed.
2. **Fast failure**: `LLM_REQUEST_TIMEOUT_SECONDS=45` (was 90). Healthy
   calls finish in ~2–20s, so 45s is generous headroom.
3. **Capped generation**: `EXTRACTION_MAX_TOKENS=3000` (was 8000).
4. **Real stage enforcement**: `TimeoutGuard.track()` wraps the block in
   `asyncio.timeout` (was warn-only) and raises `StageTimeoutError`, which
   each pipeline stage converts to a failed-stage result. Limits sit above
   the worst single call (45s timeout + 2s backoff + 45s retry ≈ 92s):
   router 100 / extractor 150 / judge 100 / ocr 120 (seconds).
   Requires Python 3.11+ (`scripts/run_all.sh` enforces this).
5. **Judge skip**: `JUDGE_SKIP_WHEN_CLEAN` is now actually wired — the Judge
   LLM stage is skipped when completeness is 100%, there are no validation
   errors, fields are non-empty, and every confidence ≥
   `JUDGE_SKIP_CONFIDENCE` (0.85). The deterministic Validator always runs.

## Measured effect

Synthetic invoice, live gateway: **72s wall** (45s transient stall +
recovery + full pipeline), 7/7 fields at 0.95 confidence,
`needs_review=False`, Judge correctly skipped. Healthy-path time without a
stall is ~25–30s. Before: ~6 min per document when stalls hit, with no
recovery (fallthrough tiers returned unparseable output).

## Knobs (`api/.env`)

```bash
LLM_REQUEST_TIMEOUT_SECONDS=45
EXTRACTION_MAX_TOKENS=3000
DISABLE_STRICT_JSON_SCHEMA=false   # KEEP false — see probe results above
JUDGE_SKIP_WHEN_CLEAN=true
JUDGE_SKIP_CONFIDENCE=0.85
ROUTER_TIMEOUT_SECONDS=100
EXTRACTOR_TIMEOUT_SECONDS=150
JUDGE_TIMEOUT_SECONDS=100
```

## Re-running the probe

```bash
source ../.venv/bin/activate   # from api/
python scripts/time_gateway_modes.py
```

If strict mode ever degrades (slow but the others fast), re-time before
changing `DISABLE_STRICT_JSON_SCHEMA` — the fallback tiers do not produce
parseable output for these schemas.
