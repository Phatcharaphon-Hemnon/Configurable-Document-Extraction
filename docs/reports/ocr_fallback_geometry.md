# OCR fallback geometry + honest handwriting block (3492511_1.pdf)

## Reproduction (isolated, caches bypassed, runtime data preserved)

- Backend: `api/` cwd, `main` @ `0bbdfe7`, `ollama-local / qwen2.5:3b`,
  `OCR_ENGINE=tesseract`, `OCR_LANGUAGES=eng+tha`, `OCR_DPI=300`,
  Tesseract 5.5.3 (`eng+tha` present), `rapidocr`/`torch`/`transformers`
  absent → RapidOCR/TrOCR recovery unavailable (recorded, not retried).
- `3492511_1.pdf` (108 543 B, 1 page, no embedded text): Tesseract
  5.7 s + 1.0 s render (7.3 s total), 27 blocks, 106 chars, coherence
  **0.35 < 0.40 → blocked**. Text: `INVOICE | 44` readable; handwriting
  misread as Thai salad (`ไ`, `เว๐`, `๐`) + caps fragments (`AWMER`,
  `KAMBERW`, `Kelana`, `caer`, `Shiv`). Review reason records engine, score,
  and `RapidOCR recovery unavailable (...)`.
- `THAI_bill.jpg`: 8.4 s, 200 blocks, 534 chars, coherence 0.63 → passes gate
  (extractor failure is separate; see `llm_classified_recovery.md`).
- Error strings in running code: `extraction_service.py:707`
  (“OCR text incoherent: … no recovery produced usable text”),
  `local_ocr.py:237/257/292` (per-engine coherence notes). No GitHub
  wording is assumed.

## Why recovery failed (per-attempt ledger)

| Attempt | Engine/model/lang | Status | Detail | Time |
|---|---|---|---|---|
| Tesseract `eng+tha` PSM 6 | attempted | 27 regions, coherence 0.35 | 5.7 s | ready |
| RapidOCR-TH fallback | unavailable | `rapidocr` not installed | 0.1 s | missing |
| Hybrid EN retry / TrOCR | skipped (opt-in, packages absent) | historic TrOCR experiment stays opt-in; not reinstalled/rerun | — | — |

No recovery was *withheld*: the bounded fallback ran and reported
unavailability. Recognition did not fail technically (Tesseract completed);
it completed but remained unreadable except `INVOICE`, `44`, `BOT.`.

## Fix (`app/services/local_ocr.py`)

- Fallback now uses `ocr_blocks` (not `_ocr_image_bytes`) so replacement text
  **always ships with boxes/engine/confidence** — never `blocks=[]`.
  Successful recovery keeps fallback geometry; the original Tesseract reading
  stays distinguishable via `engines_used` + review trail (not silently
  dropped). Geometry is validated against the source page bounds.
- Every attempt logs engine/model/lang, status, elapsed, coherence, and
  readiness; unavailable/failed/incoherent outcomes each produce distinct
  review reasons (no “no recovery produced usable text” without evidence).
- Default Thai/English workflow intact: RapidOCR-TH fallback only on
  incoherent Tesseract (never a global switch); bounded English-only retry for
  uncertain Thai-looking text lives in opt-in `hybrid_ocr.py` (confident Thai
  print skips EN; uncertain regions retry EN even when TH looks Thai).
- Cache invalidation: OCR key now includes `coherence-0.40`; result
  fingerprint includes coherence threshold + `prompts-v3-classified` +
  `client-recovery-v2-classified` (policy edits invalidate).

## Quality decision

Threshold stays **0.40** (noise ≤0.35, clean ≥0.46 measured). Layout
separators ignored, letterless numeric cells dilute but never alone condemn
(numeric-only scores 1.0 for the per-field gate). `3492511_1` stays honestly
blocked: only `INVOICE`/`44` readable — label-only OCR must not become a
successful extraction (no guessed dates, amounts, currency, names, or rows).
Readable headings do not certify unreadable rows; coherent-looking output is
never proof (per-field evidence still applies).

## Tests

`test_handwriting_ocr_recovery.py` (7) + `test_ocr_fallback.py` (geometry
preservation) + `test_security.py` (sparse vs gibberish). Full suite passes.
