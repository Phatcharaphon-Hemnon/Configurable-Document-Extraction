# Unsupported document handling

The pipeline supports exactly 3 document types (`invoice`, `purchase_order`,
`delivery_note`). Pages matching none of them (specification tables, grade
charts, reference sheets, blank forms, pages without monetary business
content) short-circuit at the Router — no extractor, validator, or judge
runs on them.

## Router

- `RoutingResponseSchema.doc_type` (`api/app/schemas/llm_schemas.py`) accepts
  a fourth value, `unsupported`, with an explicit prompt option in
  `api/app/agents/router.py` (`_ROUTER_PROMPT`): spec/grade/reference pages
  must return `unsupported` with a reason instead of forcing `invoice`.
- `RoutingDecision.doc_type` uses `RouterDocType`
  (`api/app/schemas/documents.py`), the 3 fixed types plus `unsupported`.
  Output contracts (`ExtractionResult`, `ExtractionCallResult`, extractor
  registry) keep the strict 3-type `DocType` Literal — unchanged.
- The router prompt also asks for LOW confidence (< 0.5) on guesses and for
  language detection by script majority (Latin-script business text is `en`).

## Service short-circuit (`_extract_page_impl`)

| Condition | Outcome |
|---|---|
| `doc_type == "unsupported"` | `ExtractionResult` with `doc_type="invoice"` placeholder, `failed_stage="router"`, `error` + `validation_errors` = `"Unsupported document type: this document does not match any supported type (invoice, purchase_order, delivery_note). [Router note: …]"`, `needs_review=True`, completeness 0. |
| `confidence < ROUTER_LOW_CONFIDENCE_THRESHOLD (0.5)` | Same page kept under its routed (supported) type, extractor skipped, `needs_review=True`, `validation_errors=["Router uncertain …"]`, completeness 0, judge unavailable. No `error`/`failed_stage` — the type badge stays truthful. |

The marker substring is `UNSUPPORTED_DOCUMENT_MARKER`
(`"does not match any supported type"`) — shared by the API and the
frontend; keep it stable.

## Temporal

`process_page_activity` reuses `_extract_one_page`, so both paths
short-circuit identically. The standalone `extract_activity` raises
`ValueError` for `unsupported` instead of a bare `KeyError`.

## Frontend

- `isUnsupportedDocument(doc)` (`web/src/utils/pipeline.ts`) keys off the
  marker in `error`/`validation_errors`; `getResultKind` returns a distinct
  `'unsupported'` kind (not a technical failure).
- `ExtractionTab` renders a neutral `unsupported-callout` ("Unsupported
  document type" + supported-type note) instead of the generic error or
  "Needs Review" states. `getDisplayDocType` already shows `Unclassified`
  for `failed_stage="router"`.

## Tests

`api/tests/test_unsupported_routing.py`: schema accepts `unsupported`,
short-circuit outcomes for both conditions, extractor never called,
3-type registry untouched.
