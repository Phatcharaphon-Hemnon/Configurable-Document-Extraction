# Async Extraction Jobs

> Last updated: 2026-09-04. Uploads no longer hold an HTTP connection open
> for the whole pipeline — the client gets a `job_id` instantly and polls
> for the result. Refreshing, retrying, or proxy drops can no longer kill
> an extraction.

## Flow

```
POST /api/extract (files) → 202 {job_id, status: "queued"}  (~0.1s)
        │  extraction runs in a background task
        ▼
GET /api/jobs/{job_id} → {status, result?, error?}           (poll every 2s)
        │  status: queued → completed | failed
        ▼
History tab (unchanged — reads the same job rows)
```

## Backend (`api/app/api/routes.py`, `extraction_service.py`)

- `POST /extract` validates files, creates the DB job row, schedules
  `asyncio.create_task(service.run_job(...))`, returns **202**. The
  rate-limit slot is held until the task's done-callback releases it.
- **Single-flight**: uploads are fingerprinted (SHA-256 over filenames +
  bytes). Re-uploading identical bytes while the job runs returns the SAME
  `job_id` instead of stacking a duplicate pipeline.
- `extract_group(parts, job_id=...)` reuses the caller's row; without a
  `job_id` it creates its own (unchanged behavior for tests/other callers).
- `CancelledError` (disconnect/shutdown) marks the job `failed` with a
  message instead of orphaning it as `queued`.
- **Boot-cleanup**: service init marks stale `queued` rows `failed`
  (`JobRepository.fail_stale_queued_jobs`) — no live worker can own them.
- `GET /jobs/{job_id}` returns `BatchStatusResponse`, now with an `error`
  field populated for failed jobs. `SQLiteJobStore.get()` deserializes the
  JSON-string columns (`validation_errors`, judge `issues`) and rebuilds
  the required `request` block so poll responses validate.

## Frontend (`web/src/`)

- `api/client.ts`: `extractFiles()` returns `{job_id}`; `pollJobStatus()`
  polls `GET /jobs/{id}` every 2s (10-min cap). `requestJson` reads text
  first — empty bodies now raise the meaningful fallback error instead of
  a `JSON.parse` crash.
- `useDocumentQueue`: one run per group at a time (`processingRef`);
  Retry on an `uploading` group is a no-op with an info toast.
- `HistoryTab`: 15s fetch timeouts → error UI with Retry instead of
  infinite loading.

## Verifying

```bash
# Accept must be instant, same bytes reuse the job, polls complete:
curl -F files=@receipt.png http://127.0.0.1:8000/api/extract
# → {"job_id": "...", "status": "queued"}
curl http://127.0.0.1:8000/api/jobs/<job_id>   # repeat until completed
```

Measured: 202 in ~0.1s, single-flight re-upload returns the same id in
~0.0s, poll completes with full documents. Run servers from `api/` —
`KNOWLEDGE_BASE_PATH`/`DATABASE_PATH` are cwd-relative (`run_all.sh`
handles this).
