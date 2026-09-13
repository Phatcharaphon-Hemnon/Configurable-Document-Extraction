# Handwriting recovery — 3492511_1.pdf + History reset (2026-09-12)

## The case

`api/app/data/knowledge_base/ground_truth/3492511_1.pdf` is a one-page
English **handwritten** invoice (blue ink on ruled paper) with **no embedded
text** (verified: `page.get_text()` is empty). It failed with a generic OCR
error and no usable recovery explanation.

Visual transcription (by hand, from a 150 DPI render):

- Printed: `INVOICE`, `44`, `M`, `BOT. OF`.
- Handwritten: date `26-9` + printed `19` + `67` (likely 26-9-1967, ambiguous);
  addressee `EMMERTON - LAMBERT`, `253 KINGS ROAD, CHELSEA, S.W.3`;
  seller line reads `Keith Richard(s)` (final letter + flourish ambiguous);
  items `Dress 4 10 -`, `Skirt(?) 4 15 -`, `Sequin Beret 1 10 -`,
  `Silk Shirt 6 6 -`, `Satin Trousers 3 15 -`, `Serge Trousers 2 -`,
  `Belt 10 -`, total `19 6` (pre-decimal £/s/d — must not be reinterpreted
  as decimals); crossed-out purple line + signature scrawl (decorative).

## Gold reference

Added to `ground_truth/manifest.json` (12 files / 15 pages; release subset
unchanged at 8): three **unambiguous** fields only —
`invoice_number: "44"`, `bill_to_name: "EMMERTON - LAMBERT"`,
`bill_to_address: "253 KINGS ROAD, CHELSEA, S.W.3"`.
Everything uncertain (date, seller spelling, currency, all amounts,
line-item words, crossed-out lines) is listed in `excluded_fields` with the
reasoning in `notes` — exclusions, never guessed answers.

## Reproduction (isolated storage, caches disabled)

Default engine (Tesseract `eng+tha`): **27 detected regions**, printed words
readable (`INVOICE` 0.68, `44` 0.96, `BOT.` 0.95), every handwriting region
misread as Thai-script salad (`ไ`, `เว๐`, `๐`) plus random caps
(`AWMER`, `KAMBERW`, `Kelana`, `caer`, `Shiv`). Coherence **0.35** against
threshold **0.40** → blocked. RapidOCR fallback attempted and unavailable
(`rapidocr` package not installed on this host) → original text kept with a
review reason recording engine, score, and recovery outcome.

Legacy `rapidocr_onnxruntime` 1.2.3 (Latin-only, NOT the pipeline engine —
measured for information only): 13 regions; printed text good (`INVOICE`
0.83, `44`, `BOT.OF` 0.85); handwriting fragments
(`M..LHMEKTON-AAMBERY`, `53KING8`, `ROHD`, `SeguiwBevel`, `SillkSuu`,
`410-`, `315`, `Bel`, `10`). Against the 3 gold fields: **1/3** (`44`
only) — real signal, not accuracy.

The upgraded PP-OCRv5 hybrid and TrOCR could not run on this host
(`rapidocr`, `transformers`, `torch` all absent), so hybrid behavior was
verified through mocked routing tests, not changed thresholds. Hybrid stays
**opt-in**; no accuracy gains are claimed until `benchmark_ocr.py` measures
them where models are installed.

## Fixes (all covered by tests)

- **Recovery routing** (`hybrid_ocr.py`): confident Thai print still skips
  the EN retry, but low-confidence Thai-looking output now gets an English
  recognition attempt (handwriting misread as Thai salad was previously
  terminal). TrOCR is permitted only where the **English candidate**
  supports Latin text; conflicting readings keep both alternatives + review
  reasons. CPU limits unchanged (≤10 line crops, page timeout, lines only).
- **Coherence rule** (`security.py::COHERENCE_THRESHOLD = 0.40`):
  layout-only separators (`|`, `—`, brackets) are ignored and letterless
  numeric cells dilute but never alone condemn (numeric-only pages score
  1.0 for the per-field evidence gate to judge). Measured on real
  Tesseract output — noise: ICR soup 0.33, handwriting salad 0.35;
  clean: THAI_RECEIPT 0.46, Invoice2 0.53, Invoice1 0.57, tables 0.59+,
  delivery/PO 0.62+, mixed PDF 0.65/0.72. A pre-layout block basis was
  prototyped and rejected: raw blocks invert Thai (clean receipts score
  below salad), while joining rescues soup — fragment length cannot
  separate Thai print from Thai salad; the text rule with a validated
  threshold does.
- **Gate placement**: coherence is assessed **before any LLM call**
  (previously after the router) in both in-process and Temporal execution
  (`ocr_notes` plumbing + review merge). Unreadable pages stay blocked;
  recovered content flows with `needs_review`.
- **Error message**: the generic higher-DPI advice is gone. Blocked pages
  report that transcription failed (possible handwriting/degraded print),
  which recovery was attempted, and whether a required model was
  unavailable — with the original preview, recognized text, and OCR blocks
  preserved for review.

## Accuracy report (this host)

| Engine | 3492511_1 outcome | Gold fields |
|---|---|---|
| Tesseract (default) | Blocked pre-LLM, `needs_review`, preview + 106 chars + 27 blocks preserved | 0/3 extracted (honest block, not a guess) |
| Legacy RapidOCR (info only) | Fragments incl. `44` | 1/3 |
| rapidocr PP-OCRv5 / hybrid / TrOCR | Unavailable here (packages absent) | Not measured — no claims |

Removing the error message alone was not counted as success: the page still
blocks, now with a truthful, actionable report.

## History reset

- **Origin verification**: 353 `scan.png` rows carried test-suite byte
  sizes exactly (9 = `b"\x89PNG fake"`, 3 = `b"img"`, 13 = `b"%PDF-1.4 fake"`)
  with `NULL` result payloads (predating the `result_payload` column), plus
  genuine phone-photo uploads (`X5100*.jpg`) and gold-file sources. The
  current suite was re-run post-reset and wrote **0 rows** — the clutter
  predates test isolation; a regression test now guards it
  (`test_history_isolation.py`).
- **Procedure**: confirmed zero active (queued/processing) jobs, then
  backup → wipe. The dev server kept running; a live `invoice_form.webp`
  upload orphaned by a `--reload` restart mid-session was correctly marked
  `failed` by boot-cleanup and removed singly with its source dir. A second
  long-running LLM job orphaned the same way confirmed the race this
  procedure guards against.
- **Backup**: `data/backups/20260912T031829-pre-history-reset/` —
  `canonical.db` (482 jobs, `PRAGMA integrity_check` ok, written via the
  SQLite backup API) + full `sources/` copy (8 dirs).
- **Wipe**: 482 jobs, 1207 fields, 236 judge rows, 4 pages deleted; 10
  source dirs removed. Stats read zero afterwards.
- **Live acceptance** (dev server): upload of `3492511_1.pdf` produced
  exactly one History entry under its original filename, with byte-identical
  download and page preview; a second upload (`Delivery_note2.png`) listed
  both with no synthetic entries; `DELETE /api/history` while processing
  returned **409**; after completion it returned
  `{"deleted": {..., "jobs": 2, "sources": 2}}` with empty list, zeroed
  stats, and empty sources; a subsequent upload accumulated normally.
- **Durable fix**: runtime DB untracked (`git rm --cached
  data/extraction.db` + ignore rule; file preserved; fresh installs create
  an empty schema via `Database._init_db`, no seeding). `DELETE
  /api/history` returns per-kind deleted counts and answers **409** while
  jobs are active (verified live). History UI gained a confirming
  “Clear history” action with failure display; filenames always shown
  verbatim (no name-based hiding), one job per submission with nested
  pages, no preview jobs.

## Measured limitations

- Handwritten English remains **assistance-grade**: Tesseract default
  blocks it honestly; EN/TrOCR recovery needs the pinned models installed
  and still flags every provisional reading for human review.
- Thai handwriting is unsupported. Pre-decimal and ambiguous numeric
  amounts are never reinterpreted. Coherence margins are thin by nature
  (noise ≤0.35, clean ≥0.46 at threshold 0.40) — borderline pages stay
  reviewable rather than silently dropped or burned through the LLM.
