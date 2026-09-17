# Gold-set evaluation (SROIE 8-document active suite)

Gold files and `manifest.json` v2 live in `api/app/data/knowledge_base/ground_truth`:
10 SROIE receipt images (`sroie_*.jpg`) and 10 FUNSD form images (`funsd_*.png`) with original `.box.txt` OCR annotations
and `.entities.json` extraction labels preserved alongside (both invisible to
`--all` discovery, which scans image/PDF suffixes only), plus
`review_package_funsd_sroie.json` and `review_viewer.html`. FUNSD original
`.json` annotations and typed `.derived.json` sidecars are preserved alongside.
Annotations are original dataset labels (never transcribed by the assistant);
adapters are eval-local. They are never fed into extraction prompts.
Pydantic reference contracts are in `app/schemas/evaluation.py`.

Production-comparable scope per page: `seller_name`, `seller_address`,
`total_amount` (30 supported references). `sroie_receipt_date` is preserved
but unsupported for extraction scoring (10 refs, never false negatives).
FUNSD pages carry `document_kind="form"` with null production type: the
dispatch guard returns `not_evaluated` before any pipeline call, so FUNSD
entity/relationship tasks are review-only and production metrics exclude them.
All pages are extraction-only (`routing_excluded`): router accuracy is N/A.

Run from the repository root with the configured provider in `api/.env`:

```bash
.venv/bin/python api/scripts/run_eval.py --all --gold-dir api/app/data/knowledge_base/ground_truth --output-dir .
```

The default `--gold-dir` selects this suite; explicit `--gold-dir` overrides
still work. Omit `--all` for the release subset (all 20 files).
Use `--mock --output-dir .cache/eval-smoke` only for plumbing checks; mock
reports are explicitly labeled and are not release metrics. Never run `--mock`
with the active gold dir as `--gold-dir` without a disposable copy: mock mode
writes synthetic predictions into `<gold-dir>/eval_outputs/`.

Evaluation uses an isolated catalog copy, source directory and job store, with
few-shot examples off by default. It does not pollute application history or add
gold answers to RAG. It writes incremental predictions and metrics so an interrupted
run is visibly incomplete, plus one JSON per gold file in
`ground_truth/eval_outputs/`. The single `eval_report.md` covers the full run.
`eval_artifacts/metrics.json` and `predictions.json` preserve machine-readable
evidence, including TP/FP/FN totals, scoreable/empty-scope coverage, and
router N/A status.

Field precision/recall/F1 use exact normalized field names and matching values,
with no synonym mapping; eval-local aliases apply symmetrically with collision
errors. Failed documents keep all scoreable references as false negatives
(no invented false positives). Empty-scope pages are excluded from headline
averages and counted separately. Table cells match ordered rows and exact
normalized printed column labels; null reference cells are excluded. Reports
include routing/language accuracy, table row/column coverage, review rate,
page latency and configuration. Raw artifacts include stage timings and
provider usage/retries when available.

No accuracy or latency claims exist for this suite yet: model metrics are not
evaluated. Historical scores from the retired 12-file suite must not be
presented as results for these documents.

Release pass bar: pipeline runs on sample inputs and real metrics are reported.
Final-demo accuracy thresholds, polished UI, a full security audit and a prompt
version registry are not required for this release. Release notes list agents
and actual RAG sources. CI uploads checked-in reports; it does not silently replace
live metrics with a mock run.

To compare OCR engines instead of scoring the pipeline once, use the
companion operator tool `api/scripts/benchmark_ocr.py` (same gold
references, unchanged LLM settings; see
[API scripts](api_scripts.md) and
[Thai catalogs + CPU hybrid OCR](thai_catalog_hybrid_ocr.md)).
