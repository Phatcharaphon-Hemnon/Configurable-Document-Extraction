# Page-at-a-time OCR streaming + first-page timing

`LocalOCRClient.aparse_pages()` yields one `OCRPage` per page, in document
order, as each page finishes recognition. `aparse_file()` is a collecting
wrapper over it (backward compatible). `extract_group()` consumes the stream
so page 1 is extracted, checkpointed (`save_result(status="processing")`),
and visible via `GET /jobs/{id}` + `progress{completed_pages,total_pages}`
before later pages finish OCR.

## Why only first-result time improves

Rendering (`load_page_images`, shared) still runs up front; the slow part —
per-page recognition — streams. Provider concurrency stays 1 and pages stay
sequential, so whole-document time is unchanged by design. Measured (isolated
storage, real Tesseract, mocked LLM, caches off): 3-page PDF checkpoints page
1 at ~8.5s of ~18.2s total (previously page 1 appeared only after all-page
OCR at ~16s). See the dated latency report for the full table.

## Interface notes

- `aparse_pages(data, filename, use_cache)` — whole-file memory/disk cache
  semantics identical to `aparse_file` (same `_ocr_cache_key`); cached files
  yield immediately with zero OCR. `last_pages` grows incrementally for old
  readers; new code must use yielded pages, never shared mutable state.
- `_ocr_loaded_page(loaded_page)` — single per-page implementation behind
  both entry points (tesseract / rapidocr / hybrid + RapidOCR fallback +
  coherence reviews unchanged; hybrid stays opt-in).
- `_stream_ocr_pages(part, ocr_use_cache)` (service layer) — streams on an
  unstubbed real client; any instance-level `aparse_file` override (test
  doubles) falls back to the collecting path wrapped in typed records.
  Whole-file OCR failure before the first page → file error; later
  unexpected errors propagate honestly (same as before).
- Progress total comes from cheap `_count_pages` (no OCR) before page 1, or
  grows per page when the count is undecodable. Manifest writes use the same
  count the fast path looks up; skipped when unknown.
- New response timing `time_to_first_page` (seconds since job start, present
  only when ≥1 page completed) — compare against `processing`, not nested
  stage timings.

## Judge canonical records (same change set)

`JudgeAgent.evaluate` sends ONE canonical record list
(`field:<name> | value | span`, `cell:<table>/<row>/<col> | value | span`)
instead of the prediction-JSON + provenance + identifier triple. Output
contract (`score/issues/notes`) and the unknown-field guard are unchanged, so
saved results still validate; `PROMPT_VERSION`/`JUDGE_VERSION` bumps
invalidate old page fingerprints and manifests (both embed the versions).
