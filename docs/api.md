# API layer — endpoints and app wiring

> Last updated: 2026-09-09. HTTP surface in `api/app/api/routes.py` (+
> `api/app/main.py`), served under the `/api` prefix. Flow details:
> `docs/async_jobs.md`; result shape: `docs/extraction_response_contract.md`.

## What the API layer is in this project

The **API layer** = the FastAPI surface clients use: async 202-accept +
poll job model (long uploads never hold HTTP connections), templates,
evaluation, and history — with rate limiting, audit logging, and
single-flight dedup.

## `api/routes.py` — endpoints

- `GET /` — service info (doc types, models, flags, endpoint list);
  `GET /health` — `{status: ok}`.
- `POST /extract` → **202** `{job_id, status}`: validates files
  (`input_guard`), reuses the running job for identical bytes (SHA-256
  single-flight), creates the DB row, schedules `service.run_job()` as a
  background task. Rate-limit slot held until completion.
- `GET /jobs/{job_id}` — poll status/result (`BatchStatusResponse`).
- `POST /extract/batch`, `GET /templates`, `POST /evaluate`
  (`{prediction, ground_truth, doc_type}` → precision/recall/F1).
- `GET /history`, `GET /history/stats`, `GET /history/{job_id}`,
  `DELETE /history/{job_id}` — 400 unless `DATABASE_ENABLED=true`.
- Client identity for throttling: `_get_client_id()` (request IP).

## `main.py` — app wiring

Builds `FastAPI(title, version)`, mounts `CORSMiddleware` (explicit
`FRONTEND_ORIGINS` + `*.vercel.app` preview regex), includes the router at
`/api`. Run from `api/`:

```bash
source ../.venv/bin/activate   # from api/
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Frontend `VITE_API_BASE_URL` MUST include the `/api` suffix.

## Verify

```bash
source .venv/bin/activate
python -m pytest api/tests/test_async_jobs.py api/tests/test_guards.py -q
curl http://127.0.0.1:8000/api/health
```
