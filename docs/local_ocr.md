# Local OCR Pipeline (RapidOCR compatibility)

The multilingual release defaults to Tesseract `eng+tha`; see
[multilingual OCR](multilingual_ocr.md). This document describes the optional
`OCR_ENGINE=rapidocr` implementation.

> Last updated: 2026-09-04. All uploads (images + PDFs) are OCR'd on-host.
> Single text model only — no vision model, API key, or network required.

## Overview

| Upload | OCR | Pipeline |
|--------|-----|----------|
| Images | RapidOCR directly (ONNX, CPU, ~1 s/page) | Text-only: Router → Extractor → Validator → Judge |
| PDFs | PyMuPDF renders each page at 300 DPI → RapidOCR per page | Same text-only pipeline, one result per page |

The downstream pipeline is unchanged: Router → Extractor → Validator →
Judge run on OCR text with the single configured text model
(`gpt-oss:20b` on this branch — see `docs/ai_provider.md`). Field catalog, validation rules,
`source_span` evidence, and hallucination guards all behave as before.

## How it works (`api/app/services/rapidocr_client.py`)

1. `RapidOCRClient.parse_file(bytes, filename)` detects PDF vs image
   (magic bytes + extension).
2. PDFs → PyMuPDF renders each page to PNG at `OCR_DPI` → each page OCR'd.
   Images → decoded via Pillow → OCR'd directly.
3. Raw boxes (`[box, text, confidence]` per line) are sorted
   top-to-bottom, left-to-right and grouped into lines, preserving reading
   order and approximate table-row order without inventing structure.
4. Results cached in memory by file SHA-256 (`OCR_CACHE_ENABLED=true`),
   so duplicate uploads skip OCR entirely.
5. Blocking OCR runs in `asyncio.to_thread` (`aparse_file`), keeping the
   FastAPI event loop free.
6. Every recognised token is kept (no confidence filtering) — quality
   control stays in the Validator (`source_span` must appear in OCR text)
   and Judge (score < 0.7 → `needs_review`).
7. `extraction_source="ocr"` is set on all OCR'd pages.

## RapidOCR fallback for incoherent Tesseract output

> Added 2026-09-11 (ICR salvage). `LocalOCRClient._rapid_fallback`.

When the default Tesseract text scores incoherent
(`is_ocr_text_coherent`, threshold 0.35 — e.g. Thai traineddata
rendering Latin handwriting as script salad), each page gets one bounded
RapidOCR attempt; its text replaces the Tesseract text only when coherent
itself, otherwise the original is kept and the pipeline's coherence gate
reports it. RapidOCR covers Latin+digits only, so this is strictly a
fallback — never a global switch (it cannot read Thai, and Thai pages
keep Tesseract). Failures never raise. Provenance: `ICR.png`
(Tesseract 0.20 → RapidOCR 0.90, extraction completes instead of
stalling). Tests: `api/tests/test_ocr_fallback.py`.

### Limitations
- RapidOCR is an OCR engine, not a vision LLM. Neat handwriting works;
  heavy cursive/scribbles will have low recall and surface as
  `needs_review` instead of hallucinations.
- Output is ordered plain text (not Markdown tables) — the LLM extractor
  reconstructs fields from text + catalog.
- Script support covers Latin text and digits (invoices, receipts, POs).
  Non-Latin scripts such as Thai have limited support.
- First run downloads small ONNX models (~15 MB), then caches them.

## Configuration (`api/.env`)

```bash
OCR_DPI=300              # PDF render resolution (150–600)
OCR_CACHE_ENABLED=true   # SHA-256 in-memory cache
```

## Files involved

| File | Role |
|------|------|
| `api/app/services/rapidocr_client.py` | Local OCR wrapper (lazy import, SHA-256 cache, reading-order line grouping, PDF via PyMuPDF) |
| `api/app/services/extraction_service.py` | Unified OCR path: all uploads via `self.ocr.aparse_file` (parallel); Router/Extractor/Judge text-only |
| `api/app/temporal/activities.py` | `parse_activity` uses `RapidOCRClient` |
| `api/app/core/config.py` | `OCR_DPI` setting |
| `api/requirements.txt` | `rapidocr_onnxruntime`, `pymupdf`, `pillow`, `numpy` |
| `api/tests/test_all.py` | OCR unit + wiring + `/img_test/` quality tests |

## Testing

```bash
source .venv/bin/activate
cd api
python -m pytest tests/test_all.py -v -s   # units run; quality tests need /img_test samples
python -m pytest tests/ -q                  # full suite
```

To test quality: drop sample invoices/receipts into `/img_test/` (see its
README for the suggested matrix), install OCR deps
(`pip install rapidocr_onnxruntime pymupdf pillow numpy`), then re-run
`tests/test_all.py` — the quality tests execute and print per-file OCR
stats (pages, chars, lines, seconds) plus an end-to-end check that real OCR
text flows through the pipeline.
