# Extractor transport timeout investigation — read-only (2026-09-17)

Subject: ledger-fix smoke run, `sroie_X51008142033.jpg`, extractor timeout at
~300.1s. Evidence: `logs/calibration-smoke-20260916T201707-ledgerfix/`
(report, measurements, progress log, 11-line stderr, run meta). No code,
config, `.env`, or provider interaction performed for this investigation.

## 1. Client-side vs provider-side — client deadline confirmed, server state undetermined

**Client-side enforcement is confirmed** by two stderr log lines:

- `LLM timeout exhausted job=- stage=extractor model=qwen2.5:3b attempt=1
  duration=300.1s category=timeout timeout_retries=0`
- `LLM request timed out job=- stage=extractor model=qwen2.5:3b attempts=1
  duration=300.1s category=timeout timeout=300s`

The attempt ran the full configured 300s transport ceiling
(`effective_request_s: 300.0` in the record) with a single attempt and no
retry, then the client's `asyncio.wait_for` fired. **Whether the provider
was still generating server-side is undetermined**: the record's
`server_cancellation` field is `"unknown"`, and per the harness's own rule
a client timeout never proves server cancellation. No log line shows a
provider response, error status, or completion for the extractor attempt
(the 11-line stderr contains only startup warnings plus the two timeout
lines; there is not even a router completion line — router success at
101.74s is ledger-only evidence).

## 2. Document-characteristic factors — none identified from preserved evidence

- OCR took 3.57s (`ocr`, `measured`); OCR text length is **not in the
  artifacts** (sanitization drops document text), so text-length or
  language-mix contribution cannot be assessed — insufficient evidence.
- Prompt size sent to the extractor is **not logged** anywhere in the
  preserved evidence — insufficient evidence.
- Measurable context: router completed in 101.74s on the same document and
  model, so this receipt needs ~100s+ per successful stage on this host;
  the extractor's larger output contract (full schema, up to 3000 tokens)
  plausibly needs more, but that is an inference from stage asymmetry, not
  a measured prompt-size fact.

## 3. Provider configuration + recommended next diagnostic (recommendation only)

- Effective config (read-only): `LLM_PROVIDER=ollama-local`,
  `LLM_MODEL=qwen2.5:3b`, `LLM_BASE_URL=http://localhost:11434/v1`.
  The ollama-cloud / gpt-oss:20b question is not applicable — that
  provider/model is not configured, so no status-page check is relevant.
- Recommended next step (not executed): instrument prompt-size logging
  (chars + sha only, never bodies) at the extractor transport boundary so
  the next bounded run can separate "slow provider" from "large prompt";
  then run a few more single-doc samples across varied receipt sizes
  within the same 300/600/660 bounds. No timeout value is proposed and no
  production setting is touched by this report.
