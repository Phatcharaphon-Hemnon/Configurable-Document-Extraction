# Backend

FastAPI app under `api/app/`. Run from inside `api/` so `.env` and the
`app` package resolve: `python -m uvicorn app.main:app --reload`.

## Modules

| Module | Doc |
|---|---|
| `app/schemas/documents.py` | All Pydantic contracts. Core shapes: `ExtractedField(name, value: str\|float\|date\|None, confidence, source_span, is_new_field)` and `ExtractionResult(doc_type Literal[3], fields, validation_errors, needs_review)`. |
| `app/core/config.py` | Env-driven `Settings` (AI keys, Langfuse, Temporal, parsing). |
| `app/core/security.py` | `sanitize_document_text` (prompt-injection redaction + length cap) and `check_evidence` (hallucination guard: source_span must overlap the document). |
| `app/agents/router.py` | Classifies to one of the 3 fixed types; accepts text and/or image. |
| `app/agents/extractors.py` | `InvoiceExtractor`, `PurchaseOrderExtractor`, `DeliveryNoteExtractor` + `build_extractors()` registry. Compact catalog prompt, exact name normalization, `is_new_field` detection. |
| `app/agents/validator.py` | Deterministic checks → `(validation_errors, completeness, needs_review)`. No LLM call. |
| `app/agents/judge.py` | LLM sanity score; `<0.7` → needs_review. |
| `app/services/field_catalog.py` | Catalog read/write. **Exact matching only** (`normalize_field_name`); aliases are never consulted; `add_fields()` appends AI-discovered names atomically. |
| `app/services/knowledge_base.py` | Few-shot (token-capped), ground truth, templates. |
| `app/services/extraction_service.py` | Orchestration + Langfuse tracing + auto-eval + catalog registration. |
| `app/services/client.py` | Direct OpenAI LLM transport with 3-tier JSON fallback and retries. |
| `app/observability/langfuse.py` | Optional Langfuse tracer (no-op without keys). |
| `app/temporal/` | Durable workflow variant: `parse → classify → extract → validate → judge` activities. |

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/` | Service info (doc types, model, feature flags). |
| `GET /api/health` | Liveness. |
| `POST /api/extract` | Upload images/PDFs → `FileExtractionResponse` (one `ExtractionResult` per page/document). |
| `GET /api/templates` | Field catalog per doc type. |
| `POST /api/evaluate` | Score a prediction against ground truth (precision/recall/F1 + mismatches). |
| `POST /api/extract/batch` + `GET /api/jobs/{id}` | Async batch placeholder (in-memory store). |

## Knowledge base layout

```
api/app/data/knowledge_base/
├── field_catalog/    invoice_fields.json · po_fields.json · delivery_note_fields.json
├── few_shot/         invoice/ po/ delivery_note/   (optional examples)
├── ground_truth/     <stem>.json auto-eval targets
└── documents/        sample PDFs
```
