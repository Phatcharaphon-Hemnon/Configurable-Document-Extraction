# LLM Provider Registry (`docs/llm_providers.md`)

> Design record for the multi-provider support in `api/app/core/config.py`
> (`ProviderProfile` / `LLM_PROVIDERS`) and `api/app/services/client.py`
> (`_BILLING_URLS`). User-facing setup lives in `docs/ai_provider.md`.

## Why a registry

Providers were previously 2–3 hardcoded entries plus ad-hoc key fallbacks.
Every new provider touched the same three places (base URL map, native key
map, temperature special-case) and risked them drifting apart. The registry
keeps one row per provider: `base_url`, `native_key_var`, `default_model`,
`default_temperature`, `strict_json_schema`. Precedence is unchanged
everywhere: explicit `LLM_*` env vars win, provider defaults fill the gaps,
`LLM_BASE_URL` always overrides the registry URL.

## Constraint: OpenAI-compatible only

All 10 entries speak `POST /chat/completions` with `Authorization: Bearer`.
That is why **native Claude/Anthropic is excluded**: the Messages API needs
`x-api-key` / `anthropic-version` headers and a different request body, which
the shared `AsyncOpenAI` client cannot send. Building a second transport
(adapter + retry/parse path + tests) was rejected in favor of gateway access
(OpenRouter, OpenCode Zen), which serve Claude over the compatible surface.

## Deliberate sharp edges

- **DeepSeek ships no default model.** `LLM_MODEL` is required and startup
  raises `ValueError` without it, so a missing value can never be mistaken
  for a recommendation.
- **DeepSeek starts at the `json_object` tier** (`strict_json_schema=False`).
  Its chat completions document only `text` / `json_object` response formats,
  and `json_object` requires the literal word "json" in the prompt — the
  client's `_build_json_prompt_suffix` already contains it; a test pins that.
- **OpenClaw is local-first.** Default URL is the gateway loopback port; the
  key is an operator credential for the user's own machine, never a cloud
  secret. Its Chat Completions endpoint ships disabled and must be enabled.
- **Zen free tier.** `OPENCODE_API_KEY=public` is a documented literal that
  serves only zero-cost models — useful for smoke tests, not production.

## Adding a provider later

1. Append one `ProviderProfile` row in `LLM_PROVIDERS` (keep alphabetical-ish
   grouping: clouds, gateways, local).
2. Append its billing URL in `client.py::_BILLING_URLS` (`""` when none).
3. Add the preset block in `api/.env.example` + a row/section in
   `docs/ai_provider.md`.
4. Extend `api/tests/test_llm_provider_config.py::EXPECTED_PROVIDERS`.
5. Run `ruff check api/`, `pytest api/tests/ -q`, and
   `api/scripts/time_gateway_modes.py` with a real key.

## Verification status

- Unit: `test_llm_provider_config.py` pins every row (URL, native fallback,
  default model, temperature, strict default). Live model IDs in defaults
  (e.g. `grok-4`, `gemini-2.5-flash`) are best-known values — re-check
  against provider docs and `time_gateway_modes.py` before relying on them.
