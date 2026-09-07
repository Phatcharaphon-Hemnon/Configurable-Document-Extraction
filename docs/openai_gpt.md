# LLM client (`api/app/services/client.py`)

> Last updated: 2026-09-09 (branch `provider/ollama-cloud`). Generic,
> provider-agnostic LLM transport: `LLM_PROVIDER` selects the endpoint
> (openai / ollama-cloud / ollama-local), `LLM_MODEL` the model
> (`gpt-oss:20b` on this branch). See `docs/ai_provider.md`.

## What it is

- Canonical names: `Client`, `ClientError`, `ClientResult` (no legacy aliases).
- Transport: `openai.AsyncOpenAI(api_key, base_url)` with `max_retries=0`;
  all retry/fallback logic lives in this module.
- Config: `Settings.llm_api_key`, `Settings.llm_base_url`,
  `Settings.llm_model` (+ optional per-stage
  `ROUTER/EXTRACTION/JUDGE_MODEL_NAME` overrides), `Settings.llm_temperature`
  (provider default unless a call passes `temperature` explicitly).

## Call tiers (per request)

1. `response_format=json_schema` (strict, Pydantic-patched).
2. `response_format=json_object`.
3. Plain prompt with JSON-repair instructions (does NOT resend the full
   document prompt — only the malformed output + schema).

## Resilience

- Rate limits (429/503/overloaded, non-billing): exponential backoff,
  same-call retry ×4.
- Stalls (`asyncio.TimeoutError`): same-tier retry ×1, then tier fallback.
- Billing/quota (`insufficient_quota`, `billing_hard_limit`, daily quota,
  bare 403): fail fast with `ClientError` pointing to the active provider's
  billing page.
- `extra_body={"reasoning": {"enabled": False}}` still sent when
  `disable_reasoning=True`; auto-retries once without it if rejected.
- `<think>...</think>` blocks stripped in `_try_parse` before JSON parsing.

## Observability

- `Client.last_usage` holds `{input_tokens, output_tokens, total_tokens}`
  of the last successful call; `extraction_service._agent_usage` attaches
  it to Langfuse generations. No signature changes needed.

## Verify

```bash
source .venv/bin/activate
python -m pytest api/tests/test_client_retry.py api/tests/test_client_fallback_tiers.py \
  api/tests/test_client_timeout.py api/tests/test_client_schema.py \
  api/tests/test_billing_error.py api/tests/test_think_stripping.py \
  api/tests/test_rate_limit_retry.py -q
python api/scripts/time_gateway_modes.py  # needs LLM_API_KEY
python api/scripts/run_eval.py --subset invoice_01 po_01 delivery_note_01  # live-model comparison
```
