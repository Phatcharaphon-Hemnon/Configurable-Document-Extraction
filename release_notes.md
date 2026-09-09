# Multilingual page extraction — release notes

This release retains every PDF page and its source, supports Thai/English local
OCR, and renders tables with their printed columns. Opening history loads the
saved result and source without starting another extraction. Queued uploads run
sequentially and automatically continue after a prior job completes or fails.

## Changes

- Canonical history at root `data/extraction.db`; 188 legacy jobs merged into 290
  existing jobs with backups and a repeatable migration. Full page results,
  tables, source references and progress are persisted. Old records remain readable.
- Tesseract `eng+tha` local OCR; optional RapidOCR compatibility. PDF pages and TIFF
  frames keep their order, including failed/blank pages. Mixed document types and
  languages use the same per-page pipeline.
- Source-language fields and ordered dynamic tables with cell-level confidence
  and evidence. Exact catalog-name matching, stronger evidence checks and explicit
  Judge outcomes keep unsupported predictions in review.
- Compact prompts, zero few-shot examples by default, conditional Judge skipping,
  bounded retries, page/stage timing and automatic FIFO queue continuation.
- Full gold-set evaluation plus a fixed eight-file release subset. Reference labels
  are provisional visual annotations and exclude ambiguous values explicitly.

## Agents and RAG sources

Router routes to InvoiceExtractor, PurchaseOrderExtractor or DeliveryNoteExtractor.
The deterministic Validator checks fields and table cells; Judge reviews predictions
unless the clean-output skip rule applies. Local OCR precedes these text-only agents.
Temporal uses the same page-processing activity as the in-process service.

Catalog context: `api/app/data/knowledge_base/field_catalog/*.json`.
Optional few-shot context: `api/app/data/knowledge_base/few_shot/*/*.json`, retrieved
through `app/services/rag_retriever.py`. The release evaluation uses zero examples.
`ground_truth/manifest.json` is scoring-only; it is never used as prompt context.
No external RAG source is enabled by these changes.

## Validation and metrics

See `eval.md` for the full run and `eval_report.md` for the release subset.
Machine-readable predictions and metrics are in `eval_artifacts/` and uploaded as
CI release artifacts alongside these notes. The current live run uses the configured
local `qwen2.5:1.5b` text model; low accuracy, review flags and timeouts must be read
as measured limitations. No full-pipeline speedup percentage is claimed.

Backend: 310 tests passed, 2 skipped. Ruff passed. Frontend production build and all
5 queue/polling tests passed. The Playwright page/history regression is discoverable,
but has not run: browser installation timed out. Live Temporal execution was not
verified; workflow delegation is unit-tested.

## Deployment and known limits

Use `scripts/run_all.sh` to start the application. A fresh checkout needs local
Tesseract plus English/Thai trained data; this workspace's `.local/ocr` dependencies
are ignored and are not packaged in the release. See `docs/multilingual_ocr.md`.
Only one API process should own this FIFO queue.

Previously discarded PDF pages and unsaved historical source files cannot be
reconstructed. Deleting a history record retains its original source files.
OCR geometry and dynamic columns enable richer extraction, but do not guarantee
correct transcription or model reconstruction, especially for handwriting, tiny
images and blank forms. Gold covers JPG, PNG, WebP and PDF; decoder regressions
cover JPEG, BMP, GIF and multipage TIFF additionally, without claiming accuracy
measurements on those extra formats.

The release pass bar is sample pipeline execution with reported metrics. Polished
UI, full security audit and a prompt version registry remain outside this release.
