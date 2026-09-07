# AI Provider: 3 variables control everything

> Last updated: 2026-09-09 (branch `provider/ollama-cloud`). The pipeline
> talks to any OpenAI-compatible endpoint with a single text model for all
> stages. Change provider/model by editing 3 lines in `api/.env`.

## The 3 variables

```bash
LLM_PROVIDER=ollama-cloud   # or: openai | ollama-local
LLM_API_KEY=<your key>      # the only secret (ollama-local ignores it)
LLM_MODEL=gpt-oss:20b       # single model for Router + Extractor + Judge
```

| `LLM_PROVIDER` | Base URL (automatic) | Key source |
|---|---|---|
| `openai` | `https://api.openai.com/v1` | `LLM_API_KEY` (falls back to `OPENAI_API_KEY`) |
| `ollama-cloud` | `https://ollama.com/v1` | `LLM_API_KEY` (falls back to `OLLAMA_API_KEY`) |
| `ollama-local` | `http://localhost:11434/v1` | not needed (any value works) |

Get an Ollama Cloud key at `https://ollama.com/settings/keys`.
Get an OpenAI key at `https://platform.openai.com/api-keys`.

Never commit `api/.env`. Backend keys must stay out of the frontend and out of
any `VITE_*` variable.

## This branch (`provider/ollama-cloud`)

```bash
LLM_PROVIDER=ollama-cloud
LLM_API_KEY=<paste ollama cloud key>
LLM_MODEL=gpt-oss:20b
```

`gpt-oss:20b` reasons (Harmony format, cannot be disabled) and needs
temperature `1.0` — both are automatic on this branch (`LLM_TEMPERATURE`
defaults to `1.0` for `ollama-cloud`, `0.0` elsewhere; thinking traces are
stripped before JSON parsing).

## Optional overrides

```bash
LLM_BASE_URL=             # default: provider table above
ROUTER_MODEL_NAME=        # default: LLM_MODEL (same for EXTRACTION/JUDGE)
EXTRACTION_MODEL_NAME=
JUDGE_MODEL_NAME=
LLM_TEMPERATURE=          # default: 1.0 on ollama-cloud, 0.0 elsewhere
```

## Structured-output behavior

The client retains the existing three-tier strategy:

1. `response_format={"type": "json_schema", ...}` with strict schema patching.
2. `response_format={"type": "json_object"}` fallback.
3. Prompt-level JSON repair fallback.

The client also:

- strips reasoning-model `<think>...</think>` blocks before JSON parsing;
- backs off and retries the same call for rate-limit/busy errors;
- fails fast (no retry, no tier fallback) on billing/quota errors, with a
  link to the active provider's billing page;
- falls through tiers when the provider rejects an unsupported `response_format`.

Keep `DISABLE_STRICT_JSON_SCHEMA=false` initially. After changing providers or
models, verify behavior with:

```bash
source ../.venv/bin/activate
python scripts/time_gateway_modes.py
```

Run this from `api/`. If tier 1 (strict schema) never parses on a provider,
set `DISABLE_STRICT_JSON_SCHEMA=true` and re-run the eval to compare quality.

## Key names

`Settings` exposes:

- `llm_provider` (`openai` | `ollama-cloud` | `ollama-local`; unknown values
  raise `ValueError` at startup)
- `llm_api_key`, `llm_base_url`, `llm_model`, `llm_temperature`
- `router_model_name` / `extraction_model_name` / `judge_model_name`
  (each falls back to `llm_model`)
