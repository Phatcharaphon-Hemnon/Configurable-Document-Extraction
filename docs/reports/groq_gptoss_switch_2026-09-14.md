# Switch to `openai/gpt-oss-20b` on Groq + synthetic verification (2026-09-14)

## Configuration (exact values; only `LLM_MODEL` changed)

`api/.env`: `LLM_PROVIDER=openai`, `LLM_BASE_URL=https://api.groq.com/openai/v1`,
`LLM_MODEL`: ` llama-3.1-8b-instant` → `openai/gpt-oss-20b` (sole edit;
endpoint and credentials untouched). Preserved for the probe:
`DISABLE_STRICT_JSON_SCHEMA=true`, `EXTRACTION_MAX_TOKENS=3000`,
`LLM_REQUEST_TIMEOUT_SECONDS=45`, `ROUTER/EXTRACTOR/JUDGE=100/150/100`,
no per-stage model overrides (all stages inherit), no `LLM_TEMPERATURE`
(effective 0.0 via openai profile), no `LLM_REASONING_EFFORT`.
Rollback: restore `LLM_MODEL= llama-3.1-8b-instant` (exact prior bytes,
leading space included), restart backend, re-check `GET /api/`.

## Restart (targeted, verified mechanism)

Previous backend identified by cmdline
`python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`
(PID 82814 under `bash ./run_all.sh`), matching `scripts/run_all.sh:177`.
SIGTERM to that PID only (exited cleanly; port freed by its exit, not by
port-owner killing). Relaunched with the identical command from `api/`
(`.env` resolution) into `api/logs/backend-20260914-gptoss.log`.
`GET /api/` confirms: `openai / openai/gpt-oss-20b /
https://api.groq.com/openai/v1`, status ok. Probe settings asserted equal
to backend identity before any dispatch.

## Probes (synthetic invented text only; History/catalogs untouched;
judge omitted → reported unavailable, no substitute)

Guards: timeout/rate-limit retries, tier fallbacks and corrective
generation disabled in-process (restored after); transport-level dispatch
counter with hard cap 4; stop on any HTTP error incl. first 429.
Real `RouterAgent` + real invoice `ExtractorAgent.extract_call` +
production `ValidatorAgent.validate_detailed` + catalog registration on an
isolated KB copy. `DISABLE_STRICT_JSON_SCHEMA=true` preserved, so both
probes ran tier **`json_object` — strict-schema compatibility UNTESTED**.

| # | Test | Result |
|---|---|---|
| 1 | Router `classify` synthetic invoice hint | 400 `"property 'reasoning' is unsupported"` (undocumented `extra_body` flag — Groq rejects, not ignores) → existing removal path retried same tier (counted) |
| 2 | Router retry, `json_object` | **invoice, conf 0.99, 1.8s total** |
| 3 | Extractor `extract_call` synthetic invoice+table | same 400 → same-tier retry (counted) |
| 4 | Extractor retry, `json_object` | **2.4s: 9 fields + `line_items` (2 rows)**; `invoice_number=INV-PROBE-0042` exact with verbatim span; `total_amount=27.29`; all field/cell spans verbatim; validator completeness **1.00**, `needs_review=False`, 0 rejected |

Budget final **4/4** (never exceeded). No `json_validate_failed`, no
Pydantic rejection, no truncation, no 429/401/403 — corrective path never
needed, so the syntax-correction allowance goes untested.

## Passed / failed / untested

- Passed: switch + targeted restart + identity; Router classification;
  extractor parsing with exact evidence; deterministic acceptance
  (completeness 1.0, no review flags on synthetic text); budget/cap
  accounting; existing 400-fallback path absorbing the `extra_body`
  rejection without tier change.
- Notable behavior (not a failure): every new generation burns one 400 +
  same-tier retry (~1s) because Groq rejects the legacy `extra_body`
  flag. Left as-is (designed path); removing it for Groq is a follow-up
  code decision, not taken here.
- Untested: strict-`json_schema` tier; temperature variants (0.0 kept);
  reasoning-effort values; any real document; extraction quality beyond
  synthetic-text fidelity. **No claim that this switch resolves extraction
  quality** — synthetic-text verification only.
