# Router language-tag correction

Problem (X51008042779.jpg): English receipts tagged `th`. Root cause is
upstream of the router — Tesseract with `tha` traineddata emits
Thai-looking fragments on English print, and the (small, local) router LLM
obeys the noise. The hybrid OCR latin-page post-pass only covers the
hybrid engine path, while this deployment runs default Tesseract.

## Fix (`_extract_page_impl`, step 1c)

After routing (and after the unsupported/low-confidence short-circuits),
before any extractor runs: if `routing.language == "th"` and
`page_latin_fraction([page_text]) > LATIN_PAGE_THRESHOLD` (0.70,
letters-only vote from `app/services/hybrid_ocr.py`), the tag is corrected
to `"en"` with the reason annotated
(`"... [language tag corrected th->en: Latin-majority page]"`).

Only the tag is touched — `doc_type`, confidence, and the LLM's original
reason are preserved. Genuine Thai pages (fraction ≤ 0.70) keep `th`.
Short `language` values other than `th` are never rewritten.

## Tests

`api/tests/test_unsupported_routing.py::test_th_tag_corrected_on_latin_page`
(Wan Sheng receipt text → `en` + annotated reason, extraction proceeds)
and `test_th_tag_kept_on_thai_page` (Thai invoice text → stays `th`).
