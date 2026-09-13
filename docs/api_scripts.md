# API scripts — eval, gateway probe, OCR benchmark

> Last updated: 2026-09-12. Operator tools in `api/scripts/`.
> The one-command setup is separate: `./scripts/run_all.sh` (see below).
> Covered elsewhere: `ingest_kb.py` → `docs/rag_kb.md`,
> `review_discovered_fields.py` → `docs/catalog_review.md`.

## What these scripts are in this project

**Scripts** = operator tools: model/provider diagnostics, pipeline scoring,
and OCR-engine comparison. All run with the repo venv from the repository
root (e.g. `.venv/bin/python api/scripts/run_eval.py ...`), so `.env` and
the `app` package resolve. The only setup script is `./scripts/run_all.sh`
(one-command install + run); everything under `api/scripts/` assumes an
installed checkout and performs one diagnostic or scoring job.

## `api/scripts/run_eval.py` — pipeline scoring

Scores the full pipeline (local OCR → Router → Extractor → Validator →
Judge) against the KB gold set (default: the fixed eight-file release
subset covering all three document types, Thai/English and mixed PDFs;
`--all` scores the full 11-file manifest) and writes a markdown report plus
`eval_artifacts/metrics.json` and `predictions.json` (see
[Gold-set release evaluation](evaluation.md)).

```bash
.venv/bin/python api/scripts/run_eval.py --gold-dir api/app/data/knowledge_base/ground_truth --output-dir .
.venv/bin/python api/scripts/run_eval.py --all --gold-dir api/app/data/knowledge_base/ground_truth --output-dir .
```

- Real mode (default): live LLM calls (needs `LLM_API_KEY`); per-item
  failures recorded as `failed_stage`/`error` and counted in the report.
- `--mock`: deterministic offline smoke, report labeled MOCK — plumbing
  checks only, never release metrics.
- Side effects contained: DB + audit disabled, field-catalog files
  snapshotted and restored. Exit code always 0; read the summary + report.

## `api/scripts/benchmark_ocr.py` — OCR-engine comparison

Compares `tesseract` (+fallback), `rapidocr` (PP-OCRv5 TH), and `hybrid`
(TH→EN + TrOCR) against identical gold references with unchanged LLM
settings. Reports field accuracy, table-cell accuracy, handwriting
transcription errors, review rate, CPU page latency, and peak memory —
see [Thai catalogs + CPU hybrid OCR](thai_catalog_hybrid_ocr.md).
Keep `hybrid` opt-in; claim no accuracy gains until this reports them.

```bash
.venv/bin/python api/scripts/benchmark_ocr.py --engines tesseract,rapidocr,hybrid \
  --gold-dir api/app/data/knowledge_base/ground_truth --output-dir eval_artifacts
```

## `scripts/audit_review_causes.py` — review-cause triage (read-only)

Classifies every `validation_errors` entry in `eval_artifacts/predictions.json`
into buckets (`quoted-span`, `ocr-noise`, `date-format`, `missing-required`,
`genuine-mismatch`, `judge`, `table-shape`) and writes `docs/review_triage.md`.
Re-run after each guard/model change — `quoted-span` and `ocr-noise` must
shrink while `genuine-mismatch` never gets reclassified as clean.

```bash
.venv/bin/python api/scripts/audit_review_causes.py \
  --predictions eval_artifacts/predictions.json \
  --metrics eval_artifacts/metrics.json --output docs/review_triage.md
```

## `scripts/merge_eval_runs.py` — combine chunked eval runs

The 3h execution cap forces chunked `--subset` runs. Merges two run dirs'
`predictions.json` + `metrics.partial.json` using `run_eval`'s own
`summarize`/`build_combined_report` (run B wins on overlap) into `--output`.

```bash
.venv/bin/python api/scripts/merge_eval_runs.py --a eval_rerun --b eval_rerun2 --output eval_final
```

## `scripts/time_gateway_modes.py` — gateway probe
One-off diagnostic proving which output tiers return parseable JSON on the
current provider/model: strict `json_schema` → `json_object` → plain, plus
a large-prompt strict probe (production extractor shape, ~45k chars).
60s cap per mode; prints a mode/ok/secs/detail table. Run this first after
any provider or model change — it decides `DISABLE_STRICT_JSON_SCHEMA`.

```bash
source ../.venv/bin/activate   # from api/
python scripts/time_gateway_modes.py   # no args, exit 0 always
```

## `scripts/run_all.sh` — the ONLY setup script

`./scripts/run_all.sh` from repo root does everything: checks Python 3.11+
+ Node 18+, creates `.venv`, installs API + web deps, creates `api/.env`
and `web/.env` from templates, kills stale :8000/:5173 listeners, starts
API + Web UI. Ctrl+C stops both; re-running skips finished steps.
