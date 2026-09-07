# Agents — Router, Extractors, Validator (Judge: see `judge_review.md`)

> Last updated: 2026-09-09. The four pipeline stages in `api/app/agents/`.
> Transport is `services/client.py` (`Client`); contracts are
> `schemas/llm_schemas.py`. The Judge agent is documented separately in
> `docs/judge_review.md`.

## What an agent is in this project

An **agent** = one pipeline stage with a single responsibility, taking the
previous stage's output and returning Pydantic-typed results. Router,
Extractor, and Judge call the LLM (`LLM_MODEL`); the Validator is
deterministic (no LLM) so its verdicts are reproducible and free.

Per-page flow: `Router → Extractor → Validator → Judge`.

## `agents/router.py` — Stage 2 classifier

`RouterAgent.classify(filename, text_hint, image_bytes, image_media_type)`
maps OCR text and/or image bytes to exactly one of the 3 fixed doc types.

- Prompt (`_ROUTER_PROMPT`): invoice (incl. tax invoice, POS receipt,
  billing docs) vs purchase_order vs delivery_note, plus language detection
  (`en` / `th` / `other`). The filename is only a weak hint — the decision
  comes from document content.
- Pre-processing: `sanitize_document_text()` (injection guard), trimmed to
  `ROUTER_TEXT_CHARS`.
- Calls `Client.generate_structured` (`ROUTER_MODEL_NAME`) or
  `generate_structured_with_image` (`VISION_MODEL_NAME`, legacy path) with
  `RoutingResponseSchema`, `disable_reasoning=True`, capped at
  `ROUTER_MAX_TOKENS`.
- Returns `RoutingDecision(doc_type, language, confidence, reason)`.
- Helper `image_data_url()` builds base64 data-URIs for the vision path.

## `agents/extractors.py` — Stage 3 extraction (one agent per doc type)

`InvoiceExtractor`, `PurchaseOrderExtractor`, `DeliveryNoteExtractor`
(built by `build_extractors(settings, catalog)`), all extending
`BaseExtractor.extract(text, image_bytes, image_media_type, few_shot)` →
`(fields: list[ExtractedField], new_field_names)`.

- Prompt = doc label + **compact catalog** (`FieldCatalog.compact_for_prompt`:
  name + type + required, one line each — token minimization) + optional
  few-shot examples (default OFF via `FEW_SHOT_EXAMPLES_PER_DOC_TYPE=0`) +
  `_COMMON_RULES`: use catalog names verbatim; never drop a clearly labeled
  value (invent `snake_case` + `new_field: true`); every field needs
  `source_span` + `confidence`; omit absent/placeholder/decorative text;
  digits-only numbers; extract **every** `line_items` row.
- Post-processing: `_coerce_value()` maps LLM JSON onto
  `str | float | date | None` (bool→str, numbers→float, list/dict→JSON
  string); names normalized, placeholders/duplicates dropped,
  `is_new_field` set for names outside the known set (registered into the
  catalog JSON by the service — see `docs/catalog_review.md`).

## `agents/validator.py` — Stage 4 deterministic gate

`ValidatorAgent.validate(doc_type, fields, document_text,
is_image_extraction)` → `(validation_errors, completeness_score,
needs_review)`. No LLM call.

Checks, in order:

1. Missing required catalog fields.
2. Required field present but empty.
3. Evidence: `check_evidence()` — `source_span` must appear in the text
   (substring or ≥75% token overlap), else hallucination flag.
4. `confidence < 0.6` (`LOW_CONFIDENCE_THRESHOLD`).
5. Date-typed fields that don't parse against `KNOWN_DATE_FORMATS`.

`completeness_score = filled_required / total_required`;
`needs_review = bool(validation_errors)`.

## Judge

`agents/judge.py::JudgeAgent.evaluate()` → `JudgeResult(score, issues,
notes)`; skipped entirely for clean extractions
(`JUDGE_SKIP_WHEN_CLEAN`, pass bar `JUDGE_PASS_SCORE=0.7`). Full detail:
`docs/judge_review.md`.

## Verify

```bash
source .venv/bin/activate
python -m pytest api/tests/test_pipeline.py api/tests/test_extraction_filters.py -q
```
