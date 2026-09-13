# UI terminal stage + partial-result presentation

## Problem

- OCR failure (`failed_stage="ocr"`) mapped to stage 0, leaving **Router
  appearing active** after processing had ended (screenshot 2).
- Failed extractor/validator/judge stages appeared “active” rather than
  “failed”; technical failure vs unreadable source vs partial vs completed
  were indistinguishable.
- A skipped/unavailable Judge could read as “passed”.

## Fix

- `web/src/utils/pipeline.ts`: `getPipelineStage` returns **-1** for
  `failed_stage="ocr"` (no Router/Extractor active; OCR ran before routing).
  Added `getFailedStageLabel` (OCR/Router/Extractor/Validator/Judge) and
  `getResultKind` (`technical_failure | unreadable_source | partial |
  completed`; incoherent/no-readable-text → `unreadable_source`).
- `PipelineStepper.tsx`: accepts `failedStage`; renders an explicit
  “OCR failed” badge for `ocr` and `failed` (not active) styling for other
  failed stages. Completed steps keep checkmarks; active only when no failure.
- `ExtractionTab.tsx`: header shows `failed at {Stage} · {kind}` (or kind
  alone on success); OCR errors get the title
  “OCR failed — unreadable source (no extraction attempted)”; error panels
  gain **Retry (force refresh)** (result-cache bypass) and **Retry uncached**
  (both caches bypassed) alongside the existing original-download + OCR-text
  preview. Accepted vs rejected stays separate (rejected candidates +
  structured issues); Judge line keeps “(not passed)” for skipped/unavailable.

## Tests

- `web/tests/pipelineStage.test.mjs` (4): OCR not Router-active,
  extractor stage attribution, partial vs completed, skipped Judge never
  passed. `npm test` runs 9/9 green; `npm run build` passes.
