# img_test — OCR quality samples

Drop sample documents here to test RapidOCR quality. Supported extensions:

- Images: `.png` `.jpg` `.jpeg` `.webp` `.bmp` `.tiff` `.tif` `.gif`
- PDFs: `.pdf` (each page is OCR'd separately)

Suggested set (one per case):

| File | What it proves |
|------|----------------|
| `invoice_printed.png` | Clean printed invoice (baseline OCR accuracy) |
| `invoice_handwritten.jpg` | Handwritten invoice (ICR quality) |
| `receipt_low_quality.jpg` | Low-quality / skewed scan (robustness) |
| `invoice_thai.jpg` | Thai document (set `PADDLEOCR_LANG=th`) |
| `multi_page.pdf` | Multi-page PDF (per-page splitting) |

Run the quality suite from `api/`:

```bash
source ../.venv/bin/activate
python -m pytest tests/test_all.py -v -s
```

Tests without local images still run (unit tests with mocks). Image quality
tests skip automatically when the folder is empty or RapidOCR is not
installed.
