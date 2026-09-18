# Security Guardrails — how the pipeline defends itself

> Added 2026-09-18 · applies to every LLM call (Router / Extractor / Judge).
> Full code-level inventory + accepted risks:
> [`../reports/security_audit_2026-09-18.md`](../reports/security_audit_2026-09-18.md) ·
> older implementation plan: [`../reference/ai_guardrails.md`](../reference/ai_guardrails.md)

## The one rule

**Document content is DATA, never instructions.** Every byte of document
text (OCR output, filenames, extracted values, validation findings) passes
through `app/core/security.py::sanitize_document_text` before it can reach
an LLM prompt. Nothing else talks to the model.

## Guard layers (in order of contact)

```text
upload ──► input_guard ──► content_guard ──► sanitize_document_text ──► LLM
           (MIME/size/     (length caps)     (injection patterns,            │
            dimensions)                       12k prompt cap)                │
                                                                            ▼
user  ◄── Disclaimer UI ◄── output_guard ◄── structured JSON (Pydantic) ◄── response
                                (schema + suspicious-output + redacted errors)
```

| Layer | What it stops | Code |
|---|---|---|
| Input validation | fake extensions, oversized/malformed uploads | `app/guards/input_guard.py` |
| Content limits | 50k doc chars, 12k prompt chars, 100-field cap | `app/guards/content_guard.py` |
| Injection guard | "ignore previous instructions", role hijack, `<system>` tags, prompt exfiltration, secret smuggling | `app/core/security.py` |
| Output guard | malformed/suspicious LLM output, oversized responses | `app/guards/output_guard.py` |
| PII detector | credit card / national ID / tax ID redaction in logs + traces | `app/guards/pii_detector.py` |
| Audit log | every guard event → `data/logs/security_audit.jsonl` | `app/guards/audit_logger.py` |
| Disclaimer UI | every result tab reminds users to review AI output | `web/src/components/Disclaimer.tsx` |

## Hallucination control (why `needs_review` exists)

- Every extracted field must carry a `source_span` (verbatim quote) +
  `confidence` — no evidence, no acceptance.
- The Validator checks each span deterministically against the OCR text.
- The Judge re-verifies accepted records against the source; its mechanical
  claims are themselves reconciled against backend checks
  (`app/agents/judge.py::reconcile_judge_issues`).
- Anything unresolved surfaces as `needs_review: true` — a human decides.

## Injection corpus (regression safety net)

`api/tests/fixtures/injection_corpus.txt` holds 31 adversarial payloads in
9 categories (override / role hijack / fake markers / exfiltration /
smuggled actions / secrets / fake role tags / mixed-into-business-rows /
case games). `api/tests/test_injection_corpus.py` enforces the contract:

```bash
source .venv/bin/activate
python -m pytest api/tests/test_injection_corpus.py api/tests/test_security.py api/tests/test_guards.py -q
```

Every payload must be neutralized (no injection pattern survives
sanitization) **while legitimate business content survives untouched** —
Thai text, IDs with leading zeros, amounts, and table pipes are asserted to
pass through, so the guard cannot quietly degrade extraction quality.

### Adding a payload (when a new evasion shows up in the audit log)

1. Append one line to the corpus: `category|payload` (wrap the attempt in a
   realistic document fragment — line item, note, table row).
2. Run the corpus test. If the guard catches it, you now have a permanent
   regression; if not, extend `_INJECTION_PATTERNS` in `app/core/security.py`
   first (add the test, then the pattern).

## PII handling policy

- Business documents legitimately contain names, phones, tax IDs — these are
  the product's output. PII in **accepted data is logged, not blocked**.
- Sensitive patterns (credit card, national ID, tax ID) are **redacted in
  logs and Langfuse traces** (`PII_REDACT_IN_LOGS=true` default).
- Tracing redacts via an SDK `mask` hook
  (`app/observability/langfuse.py`); if the hook is unavailable the trace is
  logged with a warning — availability over silent loss.
- No PII ever enters cache fingerprints or config fingerprints.

## UI disclaimers

`web/src/components/Disclaimer.tsx` renders a formal "AI output" notice on
the Extraction, History, and Evaluation tabs: confidence is a model
estimate (not measured accuracy) and flagged fields need human review
before use. Any new tab that displays pipeline output should reuse it.

## Accepted risks (short list)

- **Unicode-homograph evasion** — regex guard is script-agnostic on purpose
  (NFKC normalization would corrupt Thai verbatim evidence matching);
  depth-of-defense: no tool/egress access, structured output schemas, Judge
  reconciliation, human review.
- **No API authentication** — single-user local deployment; do not expose
  :8000 beyond localhost without an auth proxy.

Full list + recommendations: `docs/reports/security_audit_2026-09-18.md`.
