# API scripts — eval, gateway probe, setup

> Last updated: 2026-09-09. Runnables in `api/scripts/` + `scripts/`.
> Covered elsewhere: `ingest_kb.py` → `docs/rag_kb.md`,
> `review_discovered_fields.py` → `docs/catalog_review.md`.

## What these scripts are in this project

**Scripts** = operator tools: model/provider diagnostics, pipeline scoring,
and the one-command setup. All run with the repo venv; `api/scripts/*`
run from `api/` so `.env` and the `app` package resolve.

## `scripts/run_eval.py` — pipeline scoring

Scores the full pipeline (RapidOCR → Router → Extractor → Validator →
Judge) against the KB gold subset (default 8 docs: 3 invoice, 3 PO,
2 delivery notes) and writes a markdown report (default `eval_report.md`).

```bash
source ../.venv/bin/activate   # from api/
python scripts/run_eval.py [--mock] [--few-shot 2] [--subset po_01 ...]
                           [--concurrency 2] [--output report.md]
```

- Real mode (default): live LLM calls (needs `LLM_API_KEY`); per-item
  failures recorded as `failed_stage`/`error` and counted in the report.
- `--mock`: deterministic offline smoke (prediction = ground truth minus
  last key + one bogus field), report labeled MOCK — CI only.
- Side effects contained: DB + audit disabled, field-catalog files
  snapshotted and restored. Exit code always 0; read the summary + report.

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
