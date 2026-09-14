# xKiro provider — Phase A offline implementation (2026-09-14)

Branch: `perf/latency-opt-20260914`, HEAD `f7a48f9` (+ uncommitted).
Scope: Phase A only (code + docs + isolated tests). Phase B live
verification is **blocked** (offline-only decision). No claim is made that
xKiro extraction works live. Active deployment untouched: `api/.env`
(ignored, unmodified), History (13 jobs, unmodified), catalogs unmodified.

## 1. Correction-by-correction record

1. **Dated doc URLs, retention distinction.** Every xKiro claim in
   `docs/guides/ai_provider.md` carries exact official URLs accessed
   2026-09-14 and is labeled "documented, not live-verified". Zero
   retention is described as xKiro's *stated* policy (metadata only for
   billing/operations), explicitly distinguished from upstream providers'
   own policies (prompts are forwarded upstream —
   [privacy §2/§4](https://xkiro.com/privacy)).
2. **`reasoning_effort="none"` not a universal off-switch.** `Settings`
   and `Client._resolve_reasoning_effort` accept `none`, but code comments
   (`config.py`, `client.py`) and user docs state the limitation: support
   is per-model and an accepted request does NOT prove reasoning stopped
   (xKiro silently adjusts unsupported levels). Default stays `""`
   (byte-identical current behavior).
3. **xKiro scoping explicit.** Env override applies only to exact
   (endpoint, model) pairs in `REASONING_EFFORT_ALLOWLIST`; a test pins
   that no xKiro pair is allowlisted, so the NVIDIA-only list neither
   enables xKiro silently nor drops the setting for the wrong reason —
   unresolved env values yield inactive + unchanged requests. Legacy
   `extra_body.reasoning` is never sent alongside `reasoning_effort`
   (tested for `low` and `none`). No 400-fallback reliance: fallback fires
   only on explicit unsupported-parameter responses and never downgrades
   the output tier.
4. **402/403 tested, 500/502 pinned as limitation.** Mocked xKiro-shaped
   402 (`insufficient_quota`) fails fast in 1 attempt with a dashboard
   link; 403 (`permission_denied`) fails fast with no retry; secrets are
   redacted from errors. 500/502 surface immediately without retry —
   recorded as a **compatibility limitation** vs xKiro's retry guidance,
   not measured provider behavior.
5. **Cache behavior, not key presence.** 16 generation settings × (page
   entry + manifest): changed setting → verified miss on both layers;
   unchanged → verified hit with payload + text intact. (OCR provenance
   correctly resolves from per-call arguments, covered by the same tests.)
6. **Counts derived.** `test_registry_count_derived_from_table` compares
   `len()`/sets of registry vs expectation table; docs state the counted
   total without hardcoding transitions. `api/.env`, History, catalogs
   verified untouched (see header).

## 2. Passed tests

- `ruff check api/` clean. Backend **486 passed, 2 skipped** (was 451:
  +12 `test_xkiro_provider.py`, +17 `test_cache_generation_params.py`,
  +new `test_llm_provider_config.py` rows incl. `test_xkiro_requires_model`,
  +count test). Web untouched by Phase A (no frontend files changed).
- Registry: `xkiro → https://api.xkiro.com/v1`, native `XKIRO_API_KEY`,
  **no default model** (`LLM_MODEL` required, DeepSeek pattern);
  credential isolation both directions; explicit-key precedence;
  vendor-prefixed passthrough; all pre-existing providers byte-identical.

## 3. Documented capabilities (not live-verified)

Endpoint/auth, vendor-prefixed IDs, free/paid/premium tiers + daily
allowance, silent `response_format` cases, per-model reasoning levels
(omitted≠disabled, silent step-down), reasoning billed as output tokens,
error taxonomy + retry table, dashboard URLs — sources:
[quickstart](https://docs.xkiro.com/guides/quickstart/),
[structured-output](https://docs.xkiro.com/guides/structured-output/),
[reasoning](https://docs.xkiro.com/guides/reasoning/),
[pricing](https://docs.xkiro.com/guides/pricing/),
[tiers](https://docs.xkiro.com/models/tiers/),
[models](https://docs.xkiro.com/models/),
[errors](https://docs.xkiro.com/api/errors/),
[privacy](https://xkiro.com/privacy),
[homepage](https://xkiro.com) — all accessed 2026-09-14.

## 4. Assumptions (explicit)

- Strict-`json_schema` default left on: absence of contrary docs is not
  evidence of support; change requires documented capability or
  controlled probe evidence. Schema validation runs in every mode
  regardless (shared prompt suffix + Pydantic, no provider branch).
- 95s cutoff and "500K/day" treated as provisional (single-source).
- `XKIRO_API_KEY` as native var per user decision (docs use it;
  homepage sample shows `XTROUTER_API_KEY`).

## 5. Blocked live verification (unblock conditions)

`GET /v1/models` catalog read → tier probe (`time_gateway_modes.py`)
→ synthetic-doc smoke (isolated storage, never real docs) → pin verified
free default + strict/reasoning settings from evidence. Requires: pasted
`XKIRO_API_KEY`, explicit bounded-probe authorization (synthetic-only,
≤10 attempts, stop on 403/402). End-to-end and browser checks likewise
blocked.

## 6. Rollback (complete)

Revert the Phase A files (`config.py`, `client.py`, `.env.example`,
`ai_provider.md`, `README.md`, `AGENTS.md`, `tech-stack/tools.md`,
`test_llm_provider_config.py`, `test_xkiro_provider.py`,
`test_cache_generation_params.py`); restore prior `LLM_PROVIDER` /
`LLM_MODEL` **and** any `ROUTER/EXTRACTION/JUDGE_MODEL_NAME` overrides in
`api/.env` — a leftover xkiro vendor-prefixed `LLM_MODEL` 404s on other
providers (verify via `GET /api/` effective identity); restart the backend
(`./scripts/run_all.sh`). No data migration involved.
