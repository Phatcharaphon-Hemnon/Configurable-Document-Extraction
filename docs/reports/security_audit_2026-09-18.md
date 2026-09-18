# Security Audit Report — 2026-09-18

> Scope: prompt-injection defense, PII handling, output/input guards, error
> redaction, and user-facing disclaimers for the Configurable Document
> Extraction pipeline. Companion docs: `docs/reference/ai_guardrails.md`
> (mechanisms), `docs/reference/prompt_registry.md` (prompt versioning).

## 1. Guard inventory (all verified in code this audit)

| Layer | Mechanism | Location | Status |
|---|---|---|---|
| Prompt-injection neutralization | 10 regex pattern classes (override/role-hijack/tag-smuggling/exfiltration/action/secrets) + `[redacted-injection-attempt]` marker | `app/core/security.py::sanitize_document_text` | ✅ active on every LLM entry point (Router/Extractor/Judge) |
| Prompt text cap | 12,000-char cap with truncation marker | `app/core/security.py::MAX_PROMPT_CHARS` | ✅ |
| PII detection | credit card / national ID / tax ID (redact), email / phone (detect-only) | `app/guards/pii_detector.py` | ✅ `PII_DETECTION_ENABLED=true` default |
| PII log redaction | `PII_REDACT_IN_LOGS=true` default; audit previews only | `app/services/extraction_service.py` | ✅ |
| Tracing PII mask | SDK `mask` hook redacts PII patterns + truncates payloads in Langfuse | `app/observability/langfuse.py` | ✅ degrades to unmasked-logging warning if hook unavailable |
| Audit log | JSONL event stream: injection_attempt, pii_detected, rate_limit_exceeded, file_validation_failed, hallucination_flagged, stage_timeout, suspicious_output, content_validation_failed, … | `app/guards/audit_logger.py` → `data/logs/security_audit.jsonl` | ✅ `AUDIT_LOG_ENABLED=true` default |
| Input validation | MIME sniffing (content-based, not extension), image dimension limits, upload size limits | `app/guards/input_guard.py` | ✅ |
| Content limits | doc ≤50k chars, prompt ≤12k, field name ≤100, value ≤1000, span ≤500, ≤100 fields, ≤50 validation errors | `app/guards/content_guard.py` | ✅ |
| Output validation | LLM output cleaning, Pydantic schema validation, response length, suspicious-output detection | `app/guards/output_guard.py` | ✅ |
| Error redaction | provider error text scrubbed (`[REDACTED]`), full redacted body logged server-side only | `app/services/client.py::_redact_provider_text` | ✅ |
| Rate limiting | per-provider request pacing | `app/guards/rate_limiter.py` | ✅ |
| Stage timeouts | per-stage deadline guard with audit events | `app/guards/timeout_guard.py` | ✅ |
| Coherence gate | OCR noise gate (0.40) before any LLM call | `app/core/security.py::COHERENCE_THRESHOLD` | ✅ |
| Hallucination control | every field needs `source_span` + `confidence`; Validator + Judge flag missing evidence → `needs_review`; Judge mechanical claims reconciled against backend checks | agents + `app/services/evidence.py` | ✅ |
| Disclaimers (UI) | formal AI-output notice on Extraction / History / Evaluation tabs | `web/src/components/Disclaimer.tsx` | ✅ added this audit |

## 2. Injection corpus run (2026-09-18)

`api/tests/fixtures/injection_corpus.txt` — 31 payloads across 9 categories
(override, role, marker, exfil, action, secrets, tags, mixed-into-business-rows,
case-smuggling), each embedded in realistic document fragments (line items,
tax rows, notes).

Result: **36/36 tests passed** (`api/tests/test_injection_corpus.py`):

- Every payload trips detection pre-sanitize (`is_suspicious == true`).
- After `sanitize_document_text`, **no payload retains any injection
  pattern** (`is_suspicious == false`); every caught payload carries the
  redaction marker.
- Legitimate business content survives: Thai text (`รหัสผู้เสียภาษี`),
  IDs with leading zeros (`002043319-W`), amounts, table pipes — none
  altered by the guard.
- Oversized input (12k+ chars) is truncated with an explicit marker.
- Source-level guard: Router/Extractor/Judge modules must keep routing
  document text through the sanitizer (test fails if the reference is
  removed).

## 3. PII handling summary

- Extracted values are **accepted data** — PII in them (names, phones, tax
  IDs on business documents is the product's purpose) is **logged, not
  blocked**; credit-card/national-ID/tax-ID patterns are redacted in logs
  and Langfuse traces.
- No PII is included in cache fingerprints or config fingerprints
  (verified: `result_cache.py` hashes file bytes/OCR text only, never
  extracted values).
- Source originals are stored under `data/sources/` (page storage doc);
  deletion via History clear removes originals and previews with the jobs.

## 4. Accepted risks (known limitations)

| Risk | Rationale | Mitigation in depth |
|---|---|---|
| Unicode-homograph evasion (e.g. Cyrillic 'е' inside "ignore") | regex guard is script-agnostic by design; NFKC normalization would corrupt Thai verbatim evidence matching | No tool/egress access in the pipeline; structured-output schemas constrain the model to `{fields, tables}`; Judge + evidence reconciliation re-check claims; `needs_review` keeps humans in the loop |
| No API authentication | single-user local deployment (run_all.sh) | Do not expose :8000 beyond localhost without adding an auth proxy; `UNAUTHORIZED_ACCESS` audit event type exists for future wiring |
| Indirect prompt content in filenames | filename passes the sanitizer and is labeled a weak hint in the router prompt | sanitize_document_text applied; router prompt explicitly demotes filename authority |
| LLM may still follow unpatterned instructions | regex guard cannot be exhaustive | output schema validation + suspicious-output detection + Judge reconciliation + review workflow |

## 5. Recommendations (next steps, not blocking)

1. Add homograph/zero-width-punctuation normalization **at the evidence
   layer only** (not in prompt text) if evasion attempts appear in the audit
   log (`injection_attempt` events).
2. Add optional API-key auth middleware before any network exposure.
3. Grow the corpus as new evasion styles are observed in audit logs — the
   corpus test makes each addition a hard regression.

## 6. Verification commands

```bash
source .venv/bin/activate
python -m pytest api/tests/test_injection_corpus.py api/tests/test_security.py api/tests/test_guards.py -q
ruff check api/
```
