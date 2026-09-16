# Page results, source storage and history migration

`data/extraction.db` at the repository root is the canonical SQLite database.
Previously `data/` and `api/data/` were different databases selected by the launch
working directory. They were not duplicate copies. Runtime paths now resolve
against the repository root; KB paths resolve against `api/`.

`app/database/migrate_storage.py` backs up both databases with SQLite's backup API,
imports missing job IDs, remaps child row IDs, verifies foreign keys and records
imports. Repeating an import does not duplicate jobs. Stop API/worker writes first:

```bash
PYTHONPATH=api .venv/bin/python -m app.database.migrate_storage --target data/extraction.db --legacy api/data/extraction.db --backups data/backups
```

This workspace imported 188 legacy jobs into 290 canonical jobs (478 total).
Backups and the retired legacy database are under `data/backups/`; the old runtime
path is retired. Do not configure a second API process against the retired file.

`SQLiteJobStore` saves the complete `FileExtractionResponse` and one ordered
`extraction_pages` row per result. It checkpoints completed pages while a job is
processing, then marks the job completed/failed. Summary fields aggregate all pages
(`mixed` when appropriate). Existing single-page records remain readable, but
previously discarded pages and unsaved originals cannot be recovered automatically.

`SourceStorage` saves uploads under `data/sources/<UUID>/original`, metadata and
page PNGs. `SourceReference` carries filename, page number/count and relative API
URLs. `GET /api/sources/{UUID}` downloads the original; `/pages/{n}` serves its
preview. Inputs use UUID and positive-page validation. Deleting a history record
currently retains its source files; no automatic retention policy is introduced.

`GET /api/history/{id}` returns all stored results and progress. The History tab
opens the shared result/source viewer without submitting another extraction.
Each page can show its own type, language, review status, OCR text and preview.
Exports include the selected page's tables and source identity.

The server's FIFO job lock covers OCR and all pages. Subsequent queued jobs start
automatically after completion/failure; viewing history neither resubmits a job
nor pauses the queue. A corrupt file has a file-level error, while a readable
blank/failed page keeps its position in the result list.

Tests: `test_page_storage.py`, `test_request_queue.py`, `test_multilingual_pages.py`
and `web/tests/pageResults.spec.ts`.
