# Temporal — durable workflow mode (optional)

> Last updated: 2026-09-09. Drop-in durable alternative to the in-process
> pipeline in `api/app/temporal/`. Active only when
> `TEMPORAL_ENABLED=true`; default `false` keeps the in-process
> `extraction_service.py` path.

## What Temporal is in this project

**Temporal** = the optional durable-execution backend: the same
OCR → Router → Extractor → Validator → Judge chain expressed as a workflow
whose each step is an activity with retries, so a crashed worker resumes
instead of losing the job.

## `temporal/workflows.py` — the pipeline as code

`ExtractDocumentWorkflow.run(filename, raw_content, content_type)` loops
over page texts and calls, with per-activity timeouts and a 3-attempt retry
policy: `parse_activity` → `classify_activity` → `extract_activity` →
`validate_activity` → `judge_activity`. Returns
`{request: FileUploadMeta, documents: [...]}` mirroring the in-process
response shape.

## `temporal/activities.py` — stage wrappers

Thin, idempotent wrappers around the real implementations (safe to retry):
`parse_activity` (RapidOCR, tolerates OCR failure as `[""]`),
`classify_activity` (`RouterAgent`), `extract_activity` (per-type extractor
+ catalog registration), `validate_activity` (`ValidatorAgent`),
`judge_activity` (`JudgeAgent`, `needs_review` when score < 0.7).

## `temporal/worker.py` — the worker process

`main()` connects and runs a `Worker` hosting the workflow + activities.
Run from `api/` (needs a Temporal server, e.g. `temporal server start-dev`):

```bash
source ../.venv/bin/activate   # from api/
python -m app.temporal.worker
```

## `temporal/client.py` — connection helper

`get_temporal_client()` connects to `TEMPORAL_ADDRESS` (default
`localhost:7233`); `TASK_QUEUE` comes from `TEMPORAL_TASK_QUEUE`
(default `doc-extraction`). Used by the worker and the Temporal-enabled
service path.

## Verify

```bash
source .venv/bin/activate
TEMPORAL_ENABLED=true python -m pytest api/tests/test_async_jobs.py -q
```
