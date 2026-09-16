# Provider errors: full message, safely surfaced

> All LLM gateways are OpenAI-compatible, but every one 400s differently.
> The pipeline used to collapse that to `LLM API call failed
> (BadRequestError)`. This doc describes the redacted end-to-end surface.

## Where the real message lives

The OpenAI SDK (`openai>=1.40`) puts the gateway body in
`exc.body` (`{"error": {"message/code/param/type"}}` or flat), plus
`exc.status_code` and `exc.request_id`. `str(exc)` is only the short
message — that is why the UI never showed the cause.

`app/services/client.py::extract_provider_error` parses both body shapes
into `{error_type, status, code, param, type, message, request_id}`,
redacts secrets (`api_key/token/Bearer/sk-*/llx-*`), truncates to 2000
chars, and never keeps raw bodies/headers/keys.

## Flow

1. `Client._request_mode` logs the full redacted body server-side
   (`LLM provider error job=... stage=... model=... status=... code=...
   request_id=...`) and raises `ClientError(..., provider_details=...)`.
2. `DocumentExtractionService._extract_one_page` (`router`/`extractor`/
   `judge` stages) builds `ProviderErrorDetails(stage, provider, model,
   ...)` via `_provider_error_details` (walks `__cause__` chain) and stores
   it on `ExtractionResult.error_details`. Router/extractor failures also
   set `completeness_score=0.0` (no more false 100%).
3. `SQLiteJobStore` persists `error_details` as JSON (`error_details`
   column, migrated with `ALTER TABLE` on boot) and reconstructs it on
   `GET /jobs/{id}`, so polls and restarts keep it. In-memory store keeps
   the full `model_dump()` already.
4. UI `ExtractionTab` shows the short `Router failed: ...` line plus a
   `<details>Provider error details (redacted)</details>` block
   (provider/model/stage/status/code/request_id/message + Copy button).
   Full body stays in uvicorn logs + Langfuse `classify-document` /
   `extract-fields` generation output.
5. `api/scripts/time_gateway_modes.py` prints `provider={...}` per tier,
   so `strict vs json_object vs plain` failures are distinguishable per
   provider/model without uploading a document.

## Reading a failure

- `status=400 code=model_not_found` → wrong `LLM_MODEL` for this gateway.
- `status=400` mentioning `response_format/json_schema/strict/
  additionalProperties/anyOf` → gateway rejects strict schema shape.
- `status=400` mentioning `reasoning/extra_body/max_tokens/temperature`
  → parameter rejected; adjust sender per provider profile.
- `status=401/403` → key scope (e.g. Zen `public` vs paid key).
- `status=429/503` → throttling; client already backs off same-tier.

## Safety

- UI + job JSON carry redacted fields only. Keys/paths/URLs are stripped
  by `_redact_provider_text` (client) and `sanitize_error_message`
  (routes accept path). Full bodies are never persisted in DB columns
  beyond the redacted message.
- `APP_DEBUG` does not gate provider details (they are redacted by
  construction); it still gates unrelated internal tracebacks.
