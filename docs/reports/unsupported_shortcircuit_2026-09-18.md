# Unsupported-document short-circuit — implementation (2026-09-18)

Implements `docs/reference/unsupported_documents.md` (which existed ahead
of the code; the spec test `api/tests/test_unsupported_routing.py` was
failing at collection). Trigger: `funsd_87533049.png` (coupon registration
form) was forced into `invoice` by the Router and died in the Extractor
with `ValueError: No usable fields…` — a technical failure for a document
that is simply out of scope.

## What was built

- `schemas/documents.py`: `RouterDocType` (3 types + `"unsupported"`),
  `UNSUPPORTED_DOCUMENT_MARKER`, `ROUTER_LOW_CONFIDENCE_THRESHOLD = 0.5`.
  `RoutingDecision.doc_type` widened; `ExtractionResult` / `DOC_TYPES` /
  extractor registry untouched (3-type output invariant holds).
- `schemas/llm_schemas.py`: `RoutingResponseSchema.doc_type` + unsupported.
- `agents/router.py`: prompt names `unsupported` with examples (spec tables,
  Grade Substitutions charts, reference sheets, blank forms, no monetary
  content), confidence < 0.5 when guessing, language by script majority.
- `services/extraction_service.py` (`_extract_page_impl`, step 1b/1c):
  `unsupported` → placeholder-invoice result, `failed_stage="router"`,
  marker in error + validation_errors, completeness 0, no extractor /
  validator / judge; low confidence → same shape minus error/failed_stage
  ("Router uncertain …"); `th` tag on Latin-majority pages corrected to
  `en` with annotated reason (only the tag changes).
- `services/hybrid_ocr.py`: new `page_latin_fraction()` (letters-only vote).
- `temporal/activities.py`: `extract_activity` raises explicit `ValueError`
  on `unsupported` instead of a bare `KeyError`.
- Frontend: `isUnsupportedDocument()` + `'unsupported'` ResultKind in
  `utils/pipeline.ts`; neutral `unsupported-callout` in `ExtractionTab.tsx`
  (base `.callout` style, palette unchanged).
- Narrowing `assert routing.doc_type in (...)` after the short-circuits
  keeps every downstream registry/catalog lookup provably total.

## Implementation notes (deviations found while building)

- Short-circuit branches must NOT call `router_gen.end()` again — the router
  success path already ended it; a second end double-ends the generation in
  the Langfuse SDK ("Calling end() on an ended span"). Branches record a
  `trace.span("router-short-circuit", …)` instead (fire-and-forget).
- `_correct_router_language` initially had a dict-key typo
  (`"routing_reason" if False else "reason"`); fixed to `"reason"` — the
  actual `RoutingDecision` field.

## Verification

- `test_unsupported_routing.py`: 10/10 pass (was collection ERROR).
- Full suite: 35 failures byte-identical to the pre-change baseline
  (pre-existing branch state); `ruff` clean; `npm run build` clean.
- Live (`ollama-cloud/gpt-oss:20b`): `funsd_87533049.png` →
  `failed_stage=router` + marker + router note ("coupon registration
  form…"), zero extractor calls; `X51008099090.jpg` still routes invoice
  and keeps its tables (receipt runs vary run-to-run on temp 1.0 — one run
  hit an honest `Unparseable date '01-06-2018 14:20:08'` rejection, which is
  pre-existing date strictness, not this change).
