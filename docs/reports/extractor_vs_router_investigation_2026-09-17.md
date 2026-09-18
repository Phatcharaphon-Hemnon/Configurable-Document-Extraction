# Extractor vs router request-path investigation — read-only findings (2026-09-17)

## 1. Structural differences, router vs extractor request path

**Effectively identical transport; materially different payload:**

| Aspect | Router | Extractor |
|---|---|---|
| Client class / SDK | `Client` → shared pooled `AsyncOpenAI`, key `(endpoint, key, timeout)` | Same — separate `Client` instances, **same pooled SDK client** (same endpoint/key/timeout in calibration) |
| Endpoint / model / key | `LLM_BASE_URL`, `router_model_name` → `llm_model` (`qwen2.5:3b`), same Bearer | Same URL/key, `extraction_model_name` → same model (no per-stage overrides in `api/.env`) |
| Streaming | Non-streaming (`stream` omitted by default) | Same |
| Timeout wiring | `self._timeout` snapshotted at construction; calibration mutates settings **before** service construction, so both stages get the experimental value | Same |
| Concurrency gate | `limited_generation` semaphore, limit 1 per endpoint per loop | Same semaphore; usage is strictly sequential (service awaits router, then extractor) — no nesting, no deadlock shape |
| Response tier | `json_schema` strict-first (tiny `RoutingResponseSchema`) | Same tier mechanism, but the **full `ExtractionResponseSchema` with `strict: true`** — orders of magnitude larger schema object |
| Prompt | Router prompt + OCR text **truncated to 2000 chars** | Full OCR text + full catalog `compact_for_prompt` (~8.1k chars measured live) |
| `max_tokens` | 400 | 3000 |

**None found** in: connection pooling (shared, httpx defaults, no exhaustion shape at 1 concurrency), locks/semaphores (released by context manager; sequential use), headers/auth, streaming flags, endpoint/model params, or timeout plumbing. No unbounded pre-`wait_for` await either — prompt construction and the new `prompt_fingerprint` are sync CPU work over ~8k chars (microseconds); measured OCR was 3.5s.

## 2. Did the extractor request reach the network?

**Insufficient evidence — stalls earlier than the socket is unproven, but socket-write is also unproven.** What exists: the pre-dispatch `LLM request … stage=extractor … attempt=1` log + recorded dispatch intent prove the request reached the **in-process transport boundary**. There is no connect-vs-send timing split, no TLS/handshake marker, no bytes-written confirmation anywhere in the 8 preserved records or stderr captures. So: reached the client boundary (proven); left the socket (unknown).

## 3. Ranked hypotheses (evidence-only, no fixes)

1. **Provider-side slowness on the large strict-schema + 3000-token extractor call under CPU inference** — most consistent: identical transport succeeds for the small router call (~100–190s); prior local measurement showed ~28 tok/s prompt eval on this host; 8/8 censored-at-ceiling fits "too slow," but completion at any duration is unobserved, so this remains inference, not proof.
2. **Ollama's OpenAI-compat layer stalling on the huge `strict: true` response_format** (e.g., grammar compilation or constrained-decoding pathological on the big schema) — fits router-ok/extractor-silent split exactly; no server-side log available to confirm.
3. **In-process stall between boundary log and socket write** — least supported: nothing in the await chain between the log line and `create()` can plausibly consume 300–1200s (single `wait_for`-wrapped SDK call; no locks held).
4. **Server still generating after client timeout, queueing subsequent work** — already the harness's standing assumption; explains nothing about the first-attempt silence itself.

Discriminating measurement for a future authorized run: a streaming-probe diagnostic (first-token latency vs no-bytes-at-all) on one extractor-shaped request — distinguishes hypothesis 1/2 from 3 in a single bounded attempt.
