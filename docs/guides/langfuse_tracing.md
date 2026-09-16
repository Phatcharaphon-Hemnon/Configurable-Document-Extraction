# Langfuse Tracing

> Last updated: 2026-09-04. SDK v4 (`langfuse>=4.0`). Follows
> https://langfuse.com/docs/observability/best-practices
> (verified live against the checklist — see Audit below).
> Agent skill vendored at `skills/langfuse/` (upstream:
> https://github.com/langfuse/skills).

## What is traced

One trace per extracted page, named `extract-document`:

```
extract-document (span; input: filename + text excerpt, output: fields)
├── classify-document (generation; router model + token usage)
├── extract-fields (generation; extractor model + token usage)
├── validate-fields (span; errors, completeness)
├── judge-extraction (generation; judge model + usage) — or judge-skipped span
├── ocr-page (span; only for the image-fallback path)
├── catalog-update (span; only when new fields register)
└── auto-eval (span + auto-eval-f1 score; only on KB ground-truth match)
```

Trace-level scores: `judge-score`, `completeness`, `needs_review`
(`auto-eval-f1` when applicable). Metadata carries filename, models,
`doc_type`, `extraction_source`, field count, and `judge_skipped`.

## Design decisions

- **Manual v4 observations, not the OpenAI integration.** Three reasons:
  1. The integration ships full prompt text to Langfuse; our prompts carry
     PII, and the wrapper offers no masking hook.
  2. Our 3-tier retry would emit one generation per attempt (noise); one
     generation per logical stage reads correctly.
  3. No risk of leaking wrapper kwargs (`name`, `metadata`, …) to the
     third-party OpenAI-compatible gateway.
- **Token usage without signature changes:** `Client.last_usage`
  records each successful call's counts; the service reads it synchronously
  right after awaiting (same task — concurrency-safe). Verified `isinstance`
  dict so mocked agents yield `None`, never garbage.
- **PII mask hook** (`build_mask_function` over the project's
  `PIIDetector`): redacts detectable patterns and truncates long strings on
  every payload leaving the host. Text excerpts are additionally capped at
  1000 chars at the call site.
- **Nesting via root-handle `start_observation`.** Passing an explicit
  `trace_context` dict was verified live to orphan every observation into
  its own trace — do not reintroduce it (see Audit).
- **Spans must be ended.** Un-ended observations are never exported:
  fire-and-forget `span()` auto-ends, and every `_extract_one_page` return
  path calls `trace.end()`.
- **Opt-in only.** Without `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` the
  tracer is a no-op (unit-tested). Document content flows to the configured
  `LANGFUSE_HOST` only when keys are set.

## Configuration (`api/.env`)

```bash
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://jp.cloud.langfuse.com   # region: us/eu/jp/hipaa
```

## Audit (2026-09-04, live trace)

Fetched a production trace back via the REST API and checked it against the
best-practices page: single `extract-document` trace ✅, correct nesting ✅,
generation types with real model + token usage ✅, verb-first stable names ✅,
meaningful trace input/output ✅, scores ✅, metadata ✅. Two gaps found and
fixed during the audit: explicit `trace_context` orphaned observations
(switched to handle-based nesting), and un-ended spans were silently dropped
(root + fire-and-forget spans are now always ended).

Reference trace: `extract-document` with `classify-document`,
`extract-fields`, `validate-fields`, `judge-skipped` children and
`completeness`/`needs_review` scores (project `cmtls0ty5000had0fjo4yo6se`,
trace `47a8a7ca969777960c9083509349aba6`).

## Tests

`api/tests/test_langfuse_tracing.py` (7 tests): disabled no-op safety,
nesting/model/usage propagation, mask redaction + truncation, usage-helper
edge cases, and a full `extract_group` run asserting the emitted tree and
scores against a fake backend.
