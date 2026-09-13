# Local OCR engines (Tesseract default; RapidOCR / hybrid opt-in)

The multilingual release defaults to Tesseract `eng+tha`; see
[multilingual OCR](multilingual_ocr.md). This document describes the optional
`OCR_ENGINE=rapidocr` implementation (PP-OCRv5 Thai+English). For the
handwriting-assisted pipeline see
[Thai catalogs + CPU hybrid OCR](thai_catalog_hybrid_ocr.md).

> Last updated: 2026-09-12. All uploads (images + PDFs) are OCR'd on-host.
> Single text model only — no vision model, API key, or network required
> after models are downloaded on first run.

## Overview

| Upload | OCR | Pipeline |
|--------|-----|----------|
| Images | RapidOCR directly (ONNX, CPU, ~1 s/page) | Text-only: Router → Extractor → Validator → Judge |
| PDFs | PyMuPDF renders each page at 300 DPI → RapidOCR per page | Same text-only pipeline, one result per page |

The downstream pipeline is unchanged: Router → Extractor → Validator →
Judge run on OCR text with the single configured text model
(see `docs/guides/ai_provider.md` for the provider/model matrix). Field catalog, validation rules,
`source_span` evidence, and hallucination guards all behave as before.

## How it works (`api/app/services/rapidocr_client.py`)

Document loading and PDF rendering are shared with the default path:
`local_ocr.load_page_images` decodes every upload once (PDF via PyMuPDF at
`OCR_DPI`, images via Pillow). RapidOCR keeps image detection + recognition:

1. `RapidOCRClient.parse_file(bytes, filename)` renders pages through the
   shared loader, then recognises each page (detection once + Thai
   recognition first).
2. Raw boxes are sorted top-to-bottom, left-to-right and grouped into lines,
   preserving reading order and approximate table-row order without
   inventing structure. Wide column gaps become `" | "` separators.
3. Results memoised in memory by file SHA-256 (`OCR_CACHE_ENABLED=true`);
   the canonical fingerprinted disk + memory cache belongs to
   `LocalOCRClient`.
4. Blocking OCR runs in `asyncio.to_thread` (`aparse_file`), keeping the
   FastAPI event loop free.
5. Every recognised token is kept (no confidence filtering) — quality
   control stays in the Validator (`source_span` must appear in OCR text)
   and Judge (score < 0.7 → `needs_review`).
6. `extraction_source="ocr"` is set on all OCR'd pages.

## RapidOCR fallback for incoherent Tesseract output

> Added 2026-09-11 (ICR salvage). `LocalOCRClient._rapid_fallback`.

When the default Tesseract text scores incoherent
(`is_ocr_text_coherent`, `COHERENCE_THRESHOLD` 0.40 in
`app/core/security.py` — e.g. Thai traineddata
rendering Latin handwriting as script salad), each page gets one bounded
RapidOCR attempt (PP-OCRv5 Thai+English); its text replaces the Tesseract
text only when coherent itself, otherwise the original is kept and the
pipeline's coherence gate reports it. Failures never raise. Provenance:
`ICR.png` (Tesseract 0.20 → RapidOCR 0.90, extraction completes instead of
stalling). Tests: `api/tests/test_ocr_fallback.py`.

### Limitations
- RapidOCR is an OCR engine, not a vision LLM. Neat handwriting works;
  heavy cursive/scribbles will have low recall and surface as
  `needs_review` instead of hallucinations.
- Output is ordered plain text (not Markdown tables) — the LLM extractor
  reconstructs fields from text + catalog.
- Script support: Thai + English printed text (PP-OCRv5 mobile TH model);
  English handwriting assistance lives behind `OCR_ENGINE=hybrid`
  (see [Thai catalogs + CPU hybrid OCR](thai_catalog_hybrid_ocr.md)).
  Thai handwriting remains unsupported.
- First run downloads the ONNX models, then caches them.

## Configuration (`api/.env`)

```bash
OCR_ENGINE=rapidocr      # opt-in: rapidocr | hybrid | tesseract (default)
OCR_DPI=300              # PDF render resolution (150–600)
OCR_CACHE_ENABLED=true   # SHA-256 in-memory cache (+ fingerprinted disk cache in LocalOCRClient)
```
Hybrid extras (`HYBRID_TROCR_CONF_THRESHOLD`, `HYBRID_TROCR_MAX_REGIONS`)
are documented in [Thai catalogs + CPU hybrid OCR](thai_catalog_hybrid_ocr.md).

## Files involved

| File | Role |
|------|------|
| `api/app/services/local_ocr.py` | Shared document loading/PDF rendering (`load_page_images`), page adapter, fingerprinted cache, Tesseract + hybrid orchestration |
| `api/app/services/rapidocr_client.py` | Detection + recognition only (explicit PP-OCRv5 mobile Det CH + Rec TH/EN, lazy import, SHA-256 memo); file methods delegate rendering to the shared loader |
| `api/app/services/extraction_service.py` | Unified OCR path: all uploads via `self.ocr.aparse_file` (parallel); Router/Extractor/Judge text-only |
| `api/app/temporal/activities.py` | `parse_activity` / `parse_detailed_activity` use `LocalOCRClient` |
| `api/app/core/config.py` | `OCR_DPI`, `OCR_ENGINE`, hybrid settings |
| `api/requirements.txt` | `rapidocr==3.9.2`, `onnxruntime==1.29.0`, `pymupdf`, `pillow`, `numpy` (legacy `rapidocr_onnxruntime` retired) |
| `api/tests/test_all.py` | OCR unit + wiring + `/img_test/` quality tests |

## Testing

See [README Development(../../README.md#development) for the standard
lint/test commands, then:

```bash
python -m pytest tests/test_all.py -v -s   # units run; quality tests need /img_test samples
```

To test quality: drop sample invoices/receipts into `/img_test/` (see its
README for the suggested matrix), install OCR deps
(`pip install rapidocr==3.9.2 onnxruntime pymupdf pillow numpy`), then re-run
`tests/test_all.py` — the quality tests execute and print per-file OCR
stats (pages, chars, lines, seconds) plus an end-to-end check that real OCR
text flows through the pipeline.
