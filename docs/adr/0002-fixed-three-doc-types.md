# ADR 0002: Fixed 3-document-type schema

Date: 2026-08-24 · Status: Accepted

## Context

The previous architecture had two modes (`SCHEMA_MODE=strict|open`). Open mode
let the Router invent arbitrary document types and field names, which produced
inconsistent keys across extractions and made downstream evaluation unreliable.

## Decision

1. `doc_type` is a Pydantic `Literal["invoice", "purchase_order", "delivery_note"]`
   everywhere (schemas, router output, API). Open mode is removed.
2. One extractor agent per document type (`InvoiceExtractor`,
   `PurchaseOrderExtractor`, `DeliveryNoteExtractor`) selected from a registry.
3. Field names come from the on-disk field catalog and are matched EXACTLY
   (trim/lower/underscore normalization only). `alternative_names` in catalog
   files are ignored — no alias or synonym mapping, per project spec.
4. Labeled values whose name is not in the catalog are kept, flagged
   `is_new_field`, and appended to the catalog file automatically
   (`source: ai_discovered`) so the catalog grows with real documents.
5. Core result shape follows the project spec:
   `ExtractedField(name, value: str|float|date|None, confidence, source_span)`
   and `ExtractionResult(doc_type, fields, validation_errors, needs_review)`.

## Consequences

- Supersedes ADR 0001 (schema modes). `SCHEMA_MODE` env var removed.
- Consistent keys make precision/recall evaluation meaningful.
- New field names appear in `/api/templates` after the first document that
  uses them; reviewers can prune `ai_discovered` entries in the JSON files.
