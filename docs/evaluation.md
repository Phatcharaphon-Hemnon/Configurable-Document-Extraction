# Gold-set release evaluation

Gold files and `manifest.json` live in `api/app/data/knowledge_base/ground_truth`.
The manifest describes 11 files / 14 pages, their SHA-256 hashes, page types,
languages, scalar answers, printed table columns, cells and ambiguity exclusions.
Annotations were transcribed visually by the assistant before predictions and need
independent human adjudication. They are never fed into the extraction prompts.
Pydantic reference contracts are in `app/schemas/evaluation.py`.

Run from the repository root with the configured provider in `api/.env`:

```bash
.venv/bin/python api/scripts/run_eval.py --all --gold-dir api/app/data/knowledge_base/ground_truth --output-dir .
```

Omit `--all` for the fixed eight-file release subset covering all three document
types, Thai/English and mixed PDFs. Use `--mock --output-dir .cache/eval-smoke` only
for plumbing checks; mock reports are explicitly labeled and are not release metrics.

Evaluation uses an isolated catalog copy, source directory and job store, with
few-shot examples off by default. It does not pollute application history or add
gold answers to RAG. It writes incremental predictions and metrics so an interrupted
run is visibly incomplete, plus one JSON per gold file in
`ground_truth/eval_outputs/`. The single `eval_report.md` covers the full run
followed by the fixed release-subset section. `eval_artifacts/metrics.json` and
`predictions.json` preserve machine-readable evidence.

Field precision/recall/F1 use exact normalized field names and matching values,
with no synonym mapping. Digit-string IDs retain leading zeros. Failed or missing
pages remain in the denominator; success-only F1 is shown separately. Table cells
match ordered rows and exact normalized printed column labels; null reference
cells are excluded. Reports include routing/language accuracy, table row/column
coverage, review rate, page latency and configuration. Raw artifacts include stage
timings and provider usage/retries when available. Format decoding tests cover
additional formats absent from this small gold set; they are not accuracy metrics.

Warm OCR timings do not imply a full-pipeline speedup. No comparable baseline has
been measured. The local 1.5B text model can have low quality and timeouts; those
outcomes are part of the report, not excluded as provider failures.

Release pass bar: pipeline runs on sample inputs and real metrics are reported.
Final-demo accuracy thresholds, polished UI, a full security audit and a prompt
version registry are not required for this release. Release notes list agents
and actual RAG sources. CI uploads checked-in reports; it does not silently replace
live metrics with a mock run.
