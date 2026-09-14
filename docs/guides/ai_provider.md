# AI Provider: 3 variables control everything

> The pipeline talks to any OpenAI-compatible endpoint with a single text
> model for all stages. Change provider/model by editing 3 lines in `api/.env`.

## The 3 variables

```bash
LLM_PROVIDER=ollama-cloud   # one of the table below
LLM_API_KEY=<your key>      # the only secret (falls back to the native var)
LLM_MODEL=gpt-oss:20b       # single model for Router + Extractor + Judge
```

| `LLM_PROVIDER` | Base URL (automatic) | Key source | Default `LLM_MODEL` |
|---|---|---|---|
| `openai` | `https://api.openai.com/v1` | `LLM_API_KEY` → `OPENAI_API_KEY` | `gpt-5.4-mini` |
| `xai` | `https://api.x.ai/v1` | `LLM_API_KEY` → `XAI_API_KEY` | `grok-4` |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai/` | `LLM_API_KEY` → `GEMINI_API_KEY` | `gemini-2.5-flash` |
| `openrouter` | `https://openrouter.ai/api/v1` | `LLM_API_KEY` → `OPENROUTER_API_KEY` | `openrouter/auto` |
| `deepseek` | `https://api.deepseek.com` | `LLM_API_KEY` → `DEEPSEEK_API_KEY` | **none — `LLM_MODEL` required** |
| `kimi` | `https://api.moonshot.ai/v1` | `LLM_API_KEY` → `MOONSHOT_API_KEY` | `kimi-k2.6` |
| `ollama-cloud` | `https://ollama.com/v1` | `LLM_API_KEY` → `OLLAMA_API_KEY` | `gpt-oss:20b` |
| `ollama-local` | `http://localhost:11434/v1` | not needed | `gpt-oss:20b` |
| `mistral` | `https://api.mistral.ai/v1` | `LLM_API_KEY` → `MISTRAL_API_KEY` | `mistral-large-latest` |
| `nvidia` | `https://integrate.api.nvidia.com/v1` | `LLM_API_KEY` → `NVIDIA_API_KEY` | `meta/llama-3.3-70b-instruct` |
| `openclaw` | `http://127.0.0.1:18789/v1` | `LLM_API_KEY` → `OPENCLAW_API_KEY` (gateway token) | `openclaw/default` |
| `opencode` | `https://opencode.ai/zen/v1` | `LLM_API_KEY` → `OPENCODE_API_KEY` | `gpt-5.4-mini` |
| `xkiro` | `https://api.xkiro.com/v1` | `LLM_API_KEY` → `XKIRO_API_KEY` | **none — `LLM_MODEL` required** |

Get keys at: [OpenAI](https://platform.openai.com/api-keys) ·
[xAI](https://console.x.ai) · [Google AI Studio](https://aistudio.google.com) ·
[OpenRouter](https://openrouter.ai/settings/keys) ·
[DeepSeek](https://platform.deepseek.com) · [Moonshot](https://platform.moonshot.ai/console) ·
[Ollama](https://ollama.com/settings/keys) · [Mistral](https://console.mistral.ai) ·
[NVIDIA](https://build.nvidia.com) · [OpenCode Zen](https://opencode.ai/zen) ·
[xKiro dashboard](https://xkiro.com/dashboard/api/keys).

Never commit `api/.env`. Backend keys must stay out of the frontend and out of
any `VITE_*` variable.

## Per-provider setup

**OpenAI / xAI / Mistral** — standard cloud keys, strict `json_schema` supported:
```bash
LLM_PROVIDER=openai
LLM_API_KEY=<paste key>
LLM_MODEL=gpt-5.4-mini
```

**Gemini** — must use Google's OpenAI-compatible path (`/v1beta/openai/`,
automatic). Use a Google AI Studio key, not a Vertex credential:
```bash
LLM_PROVIDER=gemini
LLM_API_KEY=<paste Gemini API key>
```

**OpenRouter** — one key for many models (including Claude). `openrouter/auto`
routes to a working model; pin a concrete ID (e.g. `anthropic/claude-sonnet-4`)
for reproducible quality:
```bash
LLM_PROVIDER=openrouter
LLM_API_KEY=<paste key>
LLM_MODEL=openrouter/auto
```

**DeepSeek** — `LLM_MODEL` has no default and startup fails without it (by
design, so a missing value is never mistaken for a recommendation).
DeepSeek chat completions only support `json_object` (no `json_schema` tier),
so the client starts at tier 2 automatically; prompts already contain the
literal word "json" that DeepSeek requires:
```bash
LLM_PROVIDER=deepseek
LLM_API_KEY=<paste key>
LLM_MODEL=deepseek-v4-flash
```

**Kimi (Moonshot)** — global endpoint by default; China-region accounts override:
```bash
LLM_PROVIDER=kimi
LLM_API_KEY=<paste MOONSHOT_API_KEY>
# LLM_BASE_URL=https://api.moonshot.cn/v1   # China region only
```

**Ollama Cloud (default)** — `gpt-oss:20b` reasons (Harmony format, cannot be
disabled) and needs temperature `1.0`, both automatic (`LLM_TEMPERATURE`
defaults to `1.0` for `ollama-cloud`, `0.0` elsewhere; thinking traces are
stripped before JSON parsing):
```bash
LLM_PROVIDER=ollama-cloud
LLM_API_KEY=<paste ollama cloud key>
LLM_MODEL=gpt-oss:20b
```

**Nvidia (NIM)** — hosted OpenAI-compatible models at
`https://integrate.api.nvidia.com/v1`. Sign up at
[build.nvidia.com](https://build.nvidia.com) (no card; free credits for
evaluation), open any model page and click **Get API Key** (keys look like
`nvapi-...` and work catalog-wide). Structured-output note: NIM documents
`guided_json` via `extra_body`, not OpenAI `json_schema` enforcement — the
client's prompt-embedded schema + single same-tier corrective already cover
this, so no extra configuration is needed; verify with
`time_gateway_modes.py` once keyed:
```bash
LLM_PROVIDER=nvidia
LLM_API_KEY=<paste nvapi-... key>
LLM_MODEL=meta/llama-3.2-11b-vision-instruct   # default; any NIM model ID works
```
Note (2026-09-14): `meta/llama-3.3-70b-instruct` returned `410 Gone`
(EOL 2026-08-26) and was replaced as the default — see
`docs/reports/router_timeout_nvidia_2026-09-14.md` for measurements.
Timeouts: `LLM_REQUEST_TIMEOUT_SECONDS=45`, `ROUTER/JUDGE=100`,
`EXTRACTOR=150` (one retry fits inside the stage; shorter limits bound
failures without accelerating generation).

**OpenClaw (local gateway)** — the gateway's Chat Completions endpoint is
**disabled by default**; enable it first
(`gateway.http.endpoints.chatCompletions.enabled: true`), then:
```bash
LLM_PROVIDER=openclaw
LLM_API_KEY=<gateway bearer token>
LLM_MODEL=openclaw/default
```

**OpenCode Zen (gateway)** — literal key `public` serves the free-tier models;
a Zen key unlocks everything (Claude included):
```bash
LLM_PROVIDER=opencode
LLM_API_KEY=public   # or paste OPENCODE_API_KEY
```

**xKiro (gateway)** — one key for 57+ vendor-prefixed models
(`openai/...`, `anthropic/...`, ...). All claims below are
**documented 2026-09-14, not live-verified** (offline implementation; no
probe has run against this endpoint yet):
```bash
LLM_PROVIDER=xkiro
LLM_API_KEY=<paste key from https://xkiro.com/dashboard/api/keys>
LLM_MODEL=<verified vendor/model ID>   # REQUIRED: no default ships (bare names 404 upstream)
```
- Model IDs always carry the vendor prefix (`openai/gpt-5.6-sol`, never
  `gpt-5.6-sol`); the live catalog (`GET /v1/models`, incl. per-model
  `access_tier` and `reasoning_efforts`) is the source of truth —
  [models](https://docs.xkiro.com/models/),
  [tiers](https://docs.xkiro.com/models/tiers/).
- Free-tier models work on every plan within a daily token allowance;
  paid/premium need plan balance or wallet top-up (403 `permission_denied`
  otherwise) — [pricing](https://docs.xkiro.com/guides/pricing/).
- `response_format` is enforced on most models but **silently ignored** on
  DeepSeek/Qwen (prompt hint instead) and `openai/gpt-5.6-*`, `gpt-5.5`,
  `gpt-5.4*`, Claude (no error) — the client's prompt-embedded schema +
  Pydantic validation + same-tier corrective already cover this, and strict
  mode stays on until a probe proves otherwise —
  [structured output](https://docs.xkiro.com/guides/structured-output/).
- Reasoning is per-model: omitting the parameter does **not** disable it
  (many models default ON, billed as output tokens); `none` is the documented
  explicit-off value but support is per-model, and an accepted request does
  **not** prove reasoning stopped (unsupported levels are silently adjusted).
  `LLM_REASONING_EFFORT` accepts `low/medium/high/none`, defaulting to
  current behavior — [reasoning](https://docs.xkiro.com/guides/reasoning/).
- Blocking (non-streaming) requests are cut off at 95s per the quickstart —
  the 45s default request timeout stays safely under it; never run the 120s
  diagnostic pattern against xKiro — [quickstart](https://docs.xkiro.com/guides/quickstart/).
- Retention (stated policy, not verified): xKiro states zero content
  retention (metadata only for billing/operations); prompts are still
  forwarded to the upstream model provider, whose own retention policy
  applies — [privacy §2/§4](https://xkiro.com/privacy). Do not send
  documents you would not send to that upstream provider directly.
- Errors follow the OpenAI shape; retry 429/500/502/503, never blindly retry
  400/401/403/404/402 — [errors](https://docs.xkiro.com/api/errors/). Note:
  this client's transport retries 429/503 only; 500/502 surface immediately
  (compatibility limitation, not measured xKiro behavior).

**Claude** — no native entry: the Anthropic Messages API is not
OpenAI-compatible (different auth headers + body). Reach Claude through
`openrouter` (e.g. `LLM_MODEL=anthropic/claude-sonnet-4`), `opencode`, or
`xkiro`.

## Optional overrides

```bash
LLM_BASE_URL=             # default: provider table above
ROUTER_MODEL_NAME=        # default: LLM_MODEL (same for EXTRACTION/JUDGE)
EXTRACTION_MODEL_NAME=
JUDGE_MODEL_NAME=
LLM_TEMPERATURE=          # default: 1.0 on ollama-cloud, 0.0 elsewhere
LLM_REASONING_EFFORT=       # default: unset (current behavior); low/medium/high/none
                            # applies only to exact verified (endpoint, model) pairs;
                            # "none" is per-model, accepted ≠ disabled
DISABLE_STRICT_JSON_SCHEMA=  # default: true on deepseek, false elsewhere;
                             # set true/false to force either way
```

## Structured-output behavior

The client retains the existing three-tier strategy:

1. `response_format={"type": "json_schema", ...}` with strict schema patching
   (skipped when strict mode is disabled, e.g. DeepSeek default).
2. `response_format={"type": "json_object"}` fallback.
3. Prompt-level JSON repair fallback.

The client also:

- strips reasoning-model `<think>...</think>` blocks before JSON parsing;
- backs off and retries the same call for rate-limit/busy errors;
- fails fast (no retry, no tier fallback) on billing/quota errors, with a
  link to the active provider's billing page;
- falls through tiers when the provider rejects an unsupported `response_format`.

After changing providers or models, verify behavior with:

```bash
source ../.venv/bin/activate
python scripts/time_gateway_modes.py
```

Run this from `api/`. If tier 1 (strict schema) never parses on a provider,
set `DISABLE_STRICT_JSON_SCHEMA=true` and re-run the eval to compare quality.

## Key names

`Settings` exposes:

- `llm_provider` (one of the table; unknown values raise `ValueError` at startup)
- `llm_api_key`, `llm_base_url`, `llm_model`, `llm_temperature`
- `router_model_name` / `extraction_model_name` / `judge_model_name`
  (each falls back to `llm_model`)
- `disable_strict_json_schema` (env override wins, else provider default)
