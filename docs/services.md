# Services — orchestration, catalog, matching, dates

> Last updated: 2026-09-09. The业务-logic layer in `api/app/services/`.
> Companion docs own the sibling services: `client.py` → `openai_gpt.md`,
> `rapidocr_client.py` → `local_ocr.md`, `knowledge_base.py` +
> `rag_retriever.py` → `rag_kb.md`, `request_control.py` →
> `llm_request_queue.md`, `job_store.py` → `async_jobs.md`.

## What a service is in this project

A **service** = a reusable capability the agents and API layer compose:
orchestration (`extraction_service.py`), the field-name authority
(`field_catalog.py`), value comparison (`field_matching.py`), and the shared
date vocabulary (`date_formats.py`).

## `services/extraction_service.py` — the orchestrator

`DocumentExtractionService` wires the whole per-page pipeline:
RapidOCR → Router → Extractor → catalog registration → Validator → Judge.

- `extract_group(parts, job_id)` — entry for one upload (multi-file PDFs
  become one `ExtractionResult` per page); `_extract_one_page()` runs the
  stage chain with per-stage timeouts and Langfuse tracing.
- `run_job(job_id, parts)` — background-task entry used by `POST /extract`
  (202 + poll model); `create_batch()` / `get_batch_status()` back the job
  endpoints.
- `evaluate(prediction, ground_truth)` — precision/recall/F1 scoring behind
  `POST /evaluate` and `run_eval.py`.
- Helpers: `UploadedFilePart` (raw upload), `_is_image_file` /
  `_image_media_type` (routing to OCR vs vision path), `_agent_usage`
  (reads `Client.last_usage` for trace generations), `coerce_field_dates`.
- Guards composed here: `AuditLogger`, `TimeoutGuard`, `PIIDetector`,
  `ContentLimits` (`MAX_DOCUMENT_CHARS=50000`, `MAX_PROMPT_CHARS=12000`).

## `services/field_catalog.py` — field-name authority

Single source of truth for allowed field names
(`field_catalog/<type>_fields.json`); matching is EXACT after
normalization — never aliases or synonyms.

- `normalize_field_name()`: trim/lowercase, whitespace/hyphens → `_`.
- `is_placeholder_value()` (`N/A`, `-`, …), `is_sane_field_name()`
  (`^[a-z][a-z0-9_]{0,47}$`), `is_registerable_new_field()` (non-placeholder
  + sane + confidence ≥ 0.6).
- `FieldCatalog`: mtime-cached reads, `lookup()` / `known_names()`,
  `compact_for_prompt()` (name + type + required, one line each),
  atomic `add_fields()` (`source: ai_discovered`). Curating discovered
  fields: `docs/catalog_review.md`.

## `services/field_matching.py` — value comparison

`values_match(predicted, expected)` behind eval scoring: exact fast-path,
then numeric (`$€฿`, commas, `(x)` negatives, eps `1e-6`), dates (shared
formats), recursive list/dict JSON comparison, whitespace/case-insensitive
strings. `build_alternative_name_lookup()` maps canonical + alternative
names for tolerant lookups.

## `services/date_formats.py` — shared date vocabulary

`KNOWN_DATE_FORMATS` (17 entries: ISO, `d/m/Y`, `m/d/Y`, dotted, month-name
…), tried in order via `datetime.strptime`. Used by the Validator and
`field_matching`; extend here and both consumers pick it up.

## Verify

```bash
source .venv/bin/activate
python -m pytest api/tests/test_field_catalog.py api/tests/test_pipeline.py -q
```
