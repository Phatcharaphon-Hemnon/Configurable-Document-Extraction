# Frontend helpers — API client, hooks, utils

> Last updated: 2026-09-09. Non-component frontend tools in `web/src/`.
> Components/layout/behaviour: `docs/frontend.md`. Backend contracts they
> mirror: `docs/extraction_response_contract.md`.

## What frontend helpers are in this project

**Helpers** = the typed glue between React components and the backend:
fetch layer, job polling, upload/eval state hooks, and pure formatting
utilities. Components stay presentational; all async + state logic lives
here.

## `web/src/api/` — backend access

- `client.ts` — `API_BASE_URL` from `VITE_API_BASE_URL` (must include
  `/api`); `requestJson()` (text-first parse with meaningful errors);
  `extractFiles()` (POST `/extract` → `{job_id}`), `getJobStatus()` (30s
  timeout), `pollJobStatus()`, `evaluateExtraction()` (POST `/evaluate`),
  `fetchApiRoot()`.
- `jobPolling.ts` — `pollUntilTerminal()` loop (5s sleep, abort-aware)
  until job status is completed/failed; throws on unexpected states.

## `web/src/hooks/` — state

- `useDocumentQueue.ts` — upload queue state machine
  (queued→uploading→processing→done/error); one run per group
  (single-flight + AbortController), toasts on done/error/review.
- `useEvaluation.ts` — ground-truth draft per document, JSON validation,
  prefill from extracted fields, runs `/evaluate`.
- `useRecommendedModel.ts` — model chip: backend `extraction_model` value
  with local fallback.
- `useTheme.ts` — light/dark toggle persisted to `localStorage`
  (OS preference default).

## `utils/` + `lib/` + `types/`

- `utils/pipeline.ts` — `PIPELINE_STEPS`, `getPipelineStage(doc)` (0–4 from
  failed_stage/fields/validation/judge), `mergeFieldValues`,
  `formatFieldValue`.
- `lib/toast.ts` — dependency-free global toast store
  (`pushToast`/`dismissToast`/`subscribeToasts`, 4.5s auto-dismiss).
- `types/extraction.ts` — TS mirror of backend contracts (`DocType`,
  `ExtractedField`, `ExtractionResult`, `DocumentGroup`, `ApiRoot`, …);
  change backend shapes → update here too.

## Verify

```bash
cd web && npm run build   # typecheck (tsc) + vite build
```
