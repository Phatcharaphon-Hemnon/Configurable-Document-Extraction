# Multilingual local OCR

`app/services/local_ocr.py` implements the page adapter; Pydantic contracts are in
`app/schemas/ocr.py`. The default engine is Tesseract with `eng+tha` language data.
`OCR_ENGINE=rapidocr` retains the previous local engine, whose bundled recognition
model has limited Thai support. No vision API or external OCR service is used.

Install Tesseract and English/Thai trained data on the deployment host, or supply
project-local dependencies under `.local/ocr/usr/{bin,lib,share/tessdata}`. This
workspace uses that project-local layout. `TESSERACT_CMD` and `TESSDATA_DIR`
override discovery. These ignored binaries are not release assets and must be
provisioned on a fresh checkout. Missing dependencies produce explicit OCR errors.

PDF pages render at `OCR_DPI=300`; every page is OCR'd even when a PDF has native
text. Native text presence only selects grid-line preprocessing. JPEG, PNG, WebP,
BMP, GIF and TIFF use Pillow; TIFF retains every frame, GIF uses its first frame.
EXIF orientation is applied, small images are enlarged, and PNG previews are saved.
OCR boxes preserve row positions and wide column gaps. Adjacent Thai tokens are
joined. Tesseract runs in a cancellable subprocess; timeout kills and reaps it.
A failed page remains a result, and subsequent pages continue.

Languages may differ between pages or coexist on one page. Router language labels
are descriptive; values and printed table headers stay in their source language.
English API field keys still come from the exact field catalog. OCR and model
quality can remain poor on tiny text, handwriting, blank forms and skewed photos;
source evidence does not establish that OCR transcription is correct.

Existing cache configuration is project-local: `.cache/ocr-results`, bounded by
`OCR_CACHE_MAX_FILES` (128). `OCR_CACHE_ENABLED=false` disables reuse. Fingerprints
include file bytes, language data hashes, DPI, engine and preprocessing version.
A hit skips OCR only; each submitted extraction still runs its agents. No further
cache setup is needed for the current implementation.

Verification: `api/tests/test_multilingual_pages.py` decodes all supported image
formats plus multipage PDF/TIFF, with controlled OCR output to check page isolation.
Real recognition quality is measured separately by `api/scripts/run_eval.py`.
