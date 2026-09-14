# Provider compatibility matrix

How the shared client (`api/app/services/client.py` +
`api/app/services/provider_capabilities.py`) shapes requests per provider,
and what is actually verified. Status labels: **tested** (mocked-transport
unit tests), **documented** (current official docs, access date shown),
**measured** (bounded live probe with evidence), **unknown** (conservative
behavior, no claim).

Access date for all documentation below: 2026-09-14. No live provider calls
were made for this matrix beyond the cited prior probes.

## Request rules (all agents, text + legacy image paths)

- One shared builder (`build_chat_kwargs`) assembles every
  chat-completions payload from a resolved `CapabilityProfile`.
- Capabilities resolve from the EFFECTIVE (endpoint, model) — exact host
  match, no substring logic — so a native row and a base-URL override
  behave identically (pinned by parity test).
- Strict-first on unknown endpoints is an OPTIMISTIC PROBE, not a support
  claim. Prompt-embedded schema + Pydantic validation run in every mode.
- Only EXPLICIT unsupported-parameter responses are remembered (bounded
  memory: 512 entries, 24h TTL, `reset_capability_memory()`); malformed
  output, generic 400s, and truncation never change capability state, and
  never trigger tier downgrades.
- Reasoning vs output-format recovery are separate paths. A truthy
  `reasoning_effort` always replaces the legacy `extra_body` flag (never
  both). An accepted request does NOT prove reasoning changed — verify via
  output tokens + latency.
- Every HTTP dispatch counts against the shared 4-attempt generation
  budget; SDK retries stay off. The effective tier lands on
  `ClientResult.output_mode`; fingerprints carry configured policy +
  `compat_policy` version (`compat-v1`).

## Per-provider status

| Provider | Endpoint/auth | Strict tier | Reasoning | Notes |
|---|---|---|---|---|
| `openai` | documented | optimistic probe (unknown) | override inactive by default | baseline behavior |
| `xai` | documented | optimistic probe | inactive | — |
| `gemini` | documented (`/v1beta/openai/`) | optimistic probe | inactive | — |
| `openrouter` | documented | optimistic probe | inactive | gateway; Claude reachable |
| `deepseek` | documented | **unsupported** (documented; starts at `json_object`) | inactive | no default model; prompt keeps literal "json" |
| `kimi` | documented | optimistic probe | inactive | — |
| `groq` | documented | **supported** for `openai/gpt-oss-20b`/`120b`, `qwen/qwen3.8-27b` (documented); optimistic probe elsewhere | low/medium/high documented for GPT-OSS (`none` NOT valid there); `extra_body.reasoning` **measured** 400 → omitted before first request | default `openai/gpt-oss-20b` @ temp 0.6 (documented, not live-verified); free limits 30 RPM / 1K RPD / 8K TPM / 200K TPD |
| `ollama-cloud` | documented | silently ignored per prior probe (prompt schema carries) | Harmony reasoning, temp 1.0 profile | default deployment |
| `ollama-local` | loopback, no key | optimistic probe | inactive | keyless local |
| `mistral` | documented | optimistic probe | inactive | — |
| `nvidia` | documented | optimistic probe | low/medium/high **measured** (`low` accepted, 14s vs 3000-token stall) | default `meta/llama-3.2-11b-vision-instruct` |
| `openclaw` | loopback gateway token | optimistic probe | inactive | endpoint disabled by default upstream |
| `opencode` | documented | optimistic probe | inactive | `public` key = free tier |
| `xkiro` | documented | unknown (strict stays on pending probe) | per-model levels documented; no pair allowlisted (unverified effect) | no default model; silent-`response_format` cases documented |

## Error taxonomy (shared)

Retry with backoff: 429/503 (+ `Retry-After`), transport timeouts
(same-tier, budget-capped). Fail fast: billing/quota (with provider
dashboard link), 401/403/404, unknown models. 500/502 surface immediately
(compatibility limitation vs some providers' retry guidance — not measured
per provider). Secrets are redacted from all errors and logs.

## Verification status

- Tested offline: per-provider resolution, request shapes, parity
  (native vs override), fallback separation, attempt limits, backoff,
  classification, redaction, fingerprint invalidation, capability-memory
  bounds/expiry/reset (`api/tests/test_provider_compat.py`,
  `test_llm_provider_config.py`, `test_xkiro_provider.py`,
  `test_cache_generation_params.py`).
- Measured live (prior bounded probes): NVIDIA `reasoning_effort=low`
  acceptance + effect; Groq `extra_body` 400 + `json_object` extraction
  parse (see `docs/reports/`).
- Blocked: live tier/format/reasoning probes for all other providers;
  free-model default confirmation; browser checks beyond unit level.
