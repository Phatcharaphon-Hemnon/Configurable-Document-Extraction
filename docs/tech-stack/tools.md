# Tools Reference

> Last updated: 2026-09-08. Every tool this project runs on, grouped by layer.
> Overview lives in `docs/tech-stack.md`. Versions below mirror
> `api/requirements.txt` and `web/package.json`.

## At a glance

| Layer | Tool | Used for |
|---|---|---|
| Backend API | `FastAPI >=0.115` + `uvicorn[standard] >=0.30` | REST under `/api` |
| Contracts | `Pydantic >=2.7` | API + internal types (`app/schemas/`) |
| Config / Upload | `python-dotenv >=1.0`, `python-multipart >=0.0.9` | Env-driven `Settings`, multipart uploads |
| LLM transport | `openai >=1.40` (OpenAI-compatible) | Gateway calls, 3-tier structured output |
| LLM providers | 11 `LLM_PROVIDERS` entries | Model backends behind 3 env vars |
| LLM model | Single text model (`LLM_MODEL`) | Router + Extractor + Judge |
| OCR (local) | `rapidocr_onnxruntime==1.2.3`, `pymupdf>=1.24`, `pillow>=10.0`, `numpy>=1.26` | On-host text extraction |
| Persistence | stdlib `sqlite3` | Jobs, fields, judge results, error details |
| Workflow | `temporalio>=1.7` (optional) | Durable pipeline alternative |
| Observability | `langfuse>=4.0` (optional) | LLM tracing per page |
| Frontend | `React ^19.1.0`, `Vite ^7.0.4`, `TypeScript ^5.9.2` | SPA on `:5173` |
| Styling | Vanilla CSS, no UI lib | Palette-only styling |
| KB / Retrieval | JSON files + stdlib TF-IDF | Field catalog, few-shot, ground truth |
| Quality | `ruff`, `pytest`, `tsc` | Lint + tests + typecheck |
| Runtime | `Python 3.11+`, `Node 18+` | `./scripts/run_all.sh` |
| Deploy | Vercel (web) + Render/Railway/Fly.io (API) | Split hosting |

## Backend

### FastAPI + uvicorn — `api/app/main.py`, `api/app/api/routes.py`

Endpoints (all under `/api`): `GET /` (root incl. `extraction_model`),
`GET /health`, `POST /extract` (202 + `job_id`, background task), `GET
/jobs/{job_id}` (poll), `GET /templates`, `POST /api/extract/batch`, `POST
/api/evaluate`, `/history*` (list/stats/get/delete). Run from `api/` so
`.env`, the `app` package, and relative `data/extraction.db` resolve.
See `docs/api.md`.

### Pydantic — `api/app/schemas/`

Single source of truth for API and internal contracts (`documents.py`,
`llm_schemas.py`, `llm_control.py`). Core shapes: `ExtractedField`
(`source_span` + `confidence` required), `ExtractionResult` (incl.
`failed_stage`, `error_details`), `ProviderErrorDetails`. See
`docs/schemas.md`, `docs/extraction_response_contract.md`.

### python-dotenv + python-multipart — `api/app/core/config.py`

`Settings` loads `api/.env` (`LLM_PROVIDER` / `LLM_API_KEY` / `LLM_MODEL`,
`OCR_DPI`, Langfuse keys, `TEMPORAL_ENABLED`, ...). Uploads are multipart
(`MAX_UPLOAD_MB=10`). See `docs/ai_provider.md`.

### openai SDK — `api/app/services/client.py`

Provider-agnostic chat-completions client (base URL + Bearer key per
provider). Three structured-output tiers (`json_schema` → `json_object` →
prompt repair), same-tier retries for throttling, fail-fast on billing
errors, `extract_provider_error()` for redacted gateway diagnostics.
Probe any provider/model with `api/scripts/time_gateway_modes.py`
(`docs/api_scripts.md`). See `docs/ai_provider.md`,
`docs/llm_request_queue.md`, `docs/provider_errors.md`.

### LLM providers — `api/app/core/config.py::LLM_PROVIDERS`

`openai`, `xai`, `gemini`, `openrouter`, `deepseek` (ships no default
model — `LLM_MODEL` required; starts at the `json_object` tier), `kimi`,
`ollama-cloud` (default, `gpt-oss:20b` @ temperature 1.0), `ollama-local`,
`mistral`, `openclaw` (local gateway), `opencode` (Zen gateway). No native
Claude entry (Messages API isn't OpenAI-compatible) — use
`openrouter`/`opencode`. Full matrix in `docs/ai_provider.md`
(+ `docs/llm_providers.md`, `docs/openai_gpt.md`).

### Local OCR — `api/app/services/rapidocr_client.py`

RapidOCR ONNX (CPU, offline, ~1 s/page; models ~15 MB auto-download then
cached) for every upload; PDFs rendered at `OCR_DPI=300` via PyMuPDF.
SHA-256 in-memory result cache. See `docs/local_ocr.md`.

### SQLite — `api/app/database/` + `api/app/services/job_store.py`

`models.py` schema with `PRAGMA`-checked auto-migration on boot;
`job_repository.py` CRUD (tolerant `complete_job` fallback for old DB
files); `SQLiteJobStore` (in-memory fallback when
`DATABASE_ENABLED=false`). Stores jobs, fields, judge results, redacted
`error_details` JSON. Relative path → resolved from `api/`. See
`docs/database.md`, `docs/async_jobs.md`.

### Temporal — `api/app/temporal/`

Optional durable workflow (`parse → classify → extract → validate →
judge`). Default `TEMPORAL_ENABLED=false` keeps the in-process pipeline.
See `docs/temporal.md`.

### Langfuse — `api/app/observability/`

One `extract-document` trace per page with `classify-document` /
`extract-fields` / `validate-fields` / `judge-extraction` children
(token usage attached). No-op without keys. See
`docs/langfuse_tracing.md`.

## Frontend — `web/src/`

React 19 SPA (Vite 7 dev `:5173`, `tsc -b && vite build` for prod):
`api/` (client + job polling), `components/` (`ExtractionTab` with
provider-error `<details>`, `EvaluationTab`, `HistoryTab`,
`PipelineStepper`), `hooks/`, `types/`, `utils/`. Dev proxy `/api →
127.0.0.1:8000`; prod needs `VITE_API_BASE_URL=<backend>/api` (with the
`/api` suffix). Unit test: `npm test` (`tests/jobQueue.test.mjs`). See
`docs/frontend.md`, `docs/frontend_helpers.md`.

Styling is vanilla CSS (`web/src/styles.css`), no UI library — palette
only: `#CBCBCB`, `#F2F2F2`, `#174D38` (primary), `#4D1717` (danger);
light/dark via CSS vars + `localStorage`.

## Knowledge base — `api/app/data/knowledge_base/`

`field_catalog/` (exact-name field definitions, auto-extended with
`source: ai_discovered`), `few_shot/`, `ground_truth/`, `documents/`.
Retrieval is stdlib TF-IDF (`rag_retriever.py`) — no vector DB. See
`docs/rag_kb.md`, `docs/catalog_review.md`.

## Quality gates

```bash
source .venv/bin/activate
ruff check api/ && python -m pytest api/tests/ -q   # ruff.toml: py311, line-length 120
cd web && npm run build                             # typecheck + build
```

CI: `.github/workflows/ci.yml` + SonarCloud. One-command run:
`./scripts/run_all.sh` → API `:8000` + Web `:5173`.
