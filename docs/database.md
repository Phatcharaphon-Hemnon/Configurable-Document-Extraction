# Database — SQLite job persistence

> Last updated: 2026-09-09. SQLite layer in `api/app/database/` behind the
> async-jobs flow (`docs/async_jobs.md`). Enabled with `DATABASE_ENABLED`
> (default `true`), file at `DATABASE_PATH` (default
> `data/extraction.db`). Disabled → in-memory store (history lost on restart).

## What the database is in this project

The **database** = durable memory for extraction jobs: every upload's job
row, its extracted fields, and its judge verdict — powering job polling and
the History tab.

## `database/models.py` — schema

`Database(db_path)` opens SQLite with WAL mode + foreign keys, creating on
first use: `extraction_jobs` (id, filename, content_type, size, status,
doc_type, language, scores, `needs_review`, `validation_errors` JSON, error,
`failed_stage`, timestamps), `extracted_fields` (job FK cascade: name,
value, value_type, confidence, source_span, is_new_field), `judge_results`
(job FK unique: score, issues JSON, notes). Indexes on status, doc_type,
timestamps, and job ids.

## `database/job_repository.py` — data access

`JobRepository(db)`: `create_job()` → queued row; `get_job()` → job dict
with nested fields + judge; `update_job_status()`; `complete_job()` (serializes
list/dict values via `json.dumps`); `fail_stale_queued_jobs()` — marks
orphaned `queued`/`processing` rows failed at startup so no job hangs
forever; `list_jobs()` (status/doc_type filter + pagination),
`delete_job()`, `get_stats()` (totals by status/type, avg completeness,
needs-review count) for `/history/stats`.

## Verify

```bash
source .venv/bin/activate
python -m pytest api/tests/test_async_jobs.py -q
```
