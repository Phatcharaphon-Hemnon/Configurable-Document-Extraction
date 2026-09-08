# Tech Stack & What This Project Does

> Last updated: 2026-09-08. Single source for "what does this project do and what is it built with".

## 1. What this project does

**Configurable Document Extraction** extracts structured JSON from scanned / photographed business documents.

* **Fixed 3 document types:** `invoice`, `purchase_order`, `delivery_note` (enforced end-to-end by Pydantic `Literal`).
* **Upload anything:** images (PNG/JPG) + PDFs. One PDF may contain several documents — **each page becomes its own `ExtractionResult`**.
* **Pipeline (per page):**
  ```text
  Upload → RapidOCR (local text) → Router (classify 1 of 3)
    → Extractor (per doc type) → Field Catalog (exact match / auto-add)
    → Validator (rules + evidence) → Judge (LLM score) → ExtractionResult
  ```
* **Core result shape** (`api/app/schemas/documents.py`):
  ```python
  class ExtractedField(BaseModel):
      name: str
      value: str | float | date | None
      confidence: float
      source_span: str | None  # quoted evidence from document text

  class ProviderErrorDetails(BaseModel):
      stage: str | None       # router | extractor | judge
      provider: str | None    # e.g. opencode
      model: str | None
      status: int | None      # HTTP status from gateway
      code: str | None        # gateway error code (e.g. model_not_found)
      message: str | None     # redacted gateway message
      request_id: str | None

  class ExtractionResult(BaseModel):
      doc_type: Literal["invoice", "purchase_order", "delivery_note"]
      fields: list[ExtractedField]
      validation_errors: list[str]
      needs_review: bool
      completeness_score: float  # 0.0 on stage failure, never false 100%
      failed_stage: Literal["router", "extractor", "validator", "judge"] | None
      error_details: ProviderErrorDetails | None  # redacted, shown in UI <details>
  ```
* **Async jobs:** `POST /api/extract` returns `202 {job_id, status: queued}` immediately; the client polls `GET /api/jobs/{job_id}` until `completed`/`failed`. Jobs persist in SQLite (`data/extraction.db`, relative to `api/`); schema auto-migrates on boot (`PRAGMA table_info` check) and writes degrade gracefully on old DB files.
* **Key product rules:**
  1. Field names come from `api/app/data/knowledge_base/field_catalog/*.json`. Matching is EXACT (case/underscore normalization only). No aliases/synonyms. Unknown labeled values are ADDED to the catalog with `source: ai_discovered`.
  2. Every field must carry `source_span` + `confidence`. Missing evidence or low confidence → `needs_review = true`.
  3. All document text passes `sanitize_document_text` before any LLM call (prompt-injection guard).
  4. Token-minimized: compact catalog prompts, few-shot OFF by default (`FEW_SHOT_EXAMPLES_PER_DOC_TYPE=0`), reasoning disabled.
  5. Evaluate tab: score predictions vs `ground_truth/*.json` (precision / recall / F1 + mismatch table).

## 2. Tech stack overview

| Layer | Tech | Purpose / Notes |
|---|---|---|
| **Backend API** | `FastAPI >=0.115` + `uvicorn[standard] >=0.30` | REST under `/api`: `GET /api/` (root incl. `extraction_model`), `GET /api/health`, `POST /api/extract` (202 + `job_id`), `GET /api/jobs/{id}` (poll), `GET /api/templates`, `POST /api/extract/batch`, `POST /api/evaluate`, `/history*` (list/stats/get/delete). Run from `api/` so `.env` + `app` package + relative `data/extraction.db` resolve. |
| **Contracts** | `Pydantic >=2.7` | `app/schemas/` — API + internal types. Single source of truth. |
| **Config / Upload** | `python-dotenv >=1.0`, `python-multipart >=0.0.9` | Env-driven `Settings` (`app/core/config.py`); multipart file uploads, `MAX_UPLOAD_MB=10`. |
| **LLM transport** | `openai >=1.40` (OpenAI-compatible) | Talks to the active provider via `app/services/client.py`. 3-tier structured output: `json_schema` → `json_object` → repair prompt + same-tier retries. Gateway failures surface redacted `ProviderErrorDetails` (status/code/message/request_id) in logs, Langfuse, `GET /jobs` JSON, and the UI `<details>` block — never just the exception type. Probe per provider/model with `api/scripts/time_gateway_modes.py`. See `docs/ai_provider.md`, `docs/provider_errors.md`. |
| **LLM providers** | 11 OpenAI-compatible entries (`LLM_PROVIDERS`) | `openai`, `xai`, `gemini`, `openrouter`, `deepseek` (no default model — `LLM_MODEL` required; starts at `json_object` tier), `kimi`, `ollama-cloud` (default, `gpt-oss:20b` @ temp 1.0), `ollama-local`, `mistral`, `openclaw` (local gateway), `opencode` (Zen gateway). 3 vars control everything: `LLM_PROVIDER` / `LLM_API_KEY` / `LLM_MODEL` (+ optional `LLM_BASE_URL`, per-stage `*_MODEL_NAME`, `LLM_TEMPERATURE`, `DISABLE_STRICT_JSON_SCHEMA`). No native Claude entry (Messages API isn't OpenAI-compatible) — reach it via `openrouter`/`opencode`. |
| **LLM model** | Single text model (`LLM_MODEL`) for Router + Extractor + Judge | Defaults per provider (e.g. `gpt-oss:20b` on ollama-cloud, `gpt-5.4-mini` on openai/opencode). Per-stage overrides supported. No vision model required. Key = `LLM_API_KEY` (falls back to the provider's native var, e.g. `OPENAI_API_KEY`). |
| **OCR (local)** | `rapidocr_onnxruntime==1.2.3`, `pymupdf>=1.24`, `pillow>=10.0`, `numpy>=1.26` | All uploads OCR'd on-host (ONNX, CPU, offline, ~1s/page). PDFs rendered at `OCR_DPI=300` via PyMuPDF. Wrapper: `app/services/rapidocr_client.py`. SHA-256 in-memory cache. See `docs/local_ocr.md`. |
| **Persistence** | SQLite (`data/extraction.db`, stdlib `sqlite3`) | `app/database/` (`models.py` schema + `PRAGMA`-checked auto-migration) + `app/services/job_store.py` (`SQLiteJobStore`, in-memory fallback when `DATABASE_ENABLED=false`). Stores jobs, fields, judge results, and redacted `error_details` JSON. Relative path → resolved from `api/` (same CWD gotcha as `.env`). |
| **Workflow** | `temporalio>=1.7` | Optional durable workflow `parse → classify → extract → validate → judge` (`app/temporal/`). Default `TEMPORAL_ENABLED=false` = in-process pipeline. |
| **Observability** | `langfuse>=4.0` (SDK v4) | One trace `extract-document` per page with `classify-document` / `extract-fields` / `validate-fields` / `judge-extraction` children. No-op without keys. See `docs/langfuse_tracing.md`. |
| **Frontend** | `React ^19.1.0`, `react-dom ^19.1.0`, `TypeScript ^5.9.2`, `Vite ^7.0.4`, `@vitejs/plugin-react ^5.0.2` | SPA under `web/src/` (`api/`, `components/`, `hooks/`, `types/`, `utils/`). `npm run dev` → `:5173`, `npm run build` = `tsc -b && vite build`. Dev proxy `/api → 127.0.0.1:8000`. |
| **Styling** | Vanilla CSS (`web/src/styles.css`), no UI lib | Palette only: `#CBCBCB`, `#F2F2F2`, `#174D38` (primary), `#4D1717` (danger). Light/dark via CSS vars + `localStorage`. |
| **KB / Retrieval** | JSON files + stdlib TF-IDF (`rag_retriever.py`) | `field_catalog/`, `few_shot/`, `ground_truth/`, `documents/`. No vector DB. See `docs/rag_kb.md`. |
| **Quality** | `pytest`, `pytest-asyncio`, `anyio`, `ruff` | `ruff check` (`ruff.toml`, `py311`, line-length 120) + `pytest api/tests/ -q` (incl. `test_provider_error_details.py`, `test_db_migration.py`, `test_client_fallback_tiers.py`, `test_pipeline.py`) + `cd web && npm run build`. CI: `.github/workflows/ci.yml` + SonarCloud. |
| **Runtime reqs** | `Python 3.11+`, `Node 18+` | One-command setup+run: `./scripts/run_all.sh` → API `:8000` + Web `:5173`. |
| **Deploy** | Vercel (web) + Render/Railway/Fly.io (API) | Web root = `web/`, `VITE_API_BASE_URL=<backend>/api`. Backend start: `pip install -r api/requirements.txt && cd api && uvicorn app.main:app`. |

## 3. How the pieces fit

```mermaid
flowchart LR
    U[Upload<br/>images / PDF] --> P[RapidOCR<br/>local, one text per page]
    P --> R[R Router<br/>active LLM_MODEL]
    R --> E[Extractor x3<br/>catalog-constrained JSON]
    E --> C[(Field Catalog<br/>exact names)]
    E --> VA[Validator<br/>deterministic]
    VA --> J[Judge<br/>LLM score]
    J --> O[ExtractionResult<br/>+ error_details on failure]
    O --> DB[(SQLite<br/>jobs + fields)]
    LF[Langfuse] -.traces.-> R & E & VA & J
    T[Temporal] -.optional.-> R
    WEB[React 19 + Vite<br/>:5173] <--/api--> API[FastAPI<br/>:8000]
```

* **Backend modules:** see `docs/backend.md` — `agents/` (router/extractors/validator/judge), `core/` (config/security), `services/` (orchestration/LLM/client+`extract_provider_error`/catalog/KB/OCR/job_store), `database/` (models+migration/repository), `observability/`, `temporal/`, `api/routes.py` (202-accept + poll).
* **Frontend modules:** see `docs/frontend.md` — `Sidebar` (dropzone+queue), `ExtractionTab` (fields+confidence+source_span+provider-error `<details>`), `EvaluationTab` (F1/mismatches), `HistoryTab` (job list/stats), `PipelineStepper`, `hooks/useDocumentQueue|useEvaluation|useRecommendedModel` (header pill falls back to `gpt-oss:20b`).
* **Security:** `sanitize_document_text` redacts instruction patterns/role tags + length-caps; `check_evidence` requires ≥75% token overlap or substring; provider messages are redacted (`_redact_provider_text` + `sanitize_error_message`) so keys never reach UI/DB.
* **Tokens:** compact catalog (name+type+required per line), 12k char text cap, `ROUTER_TEXT_CHARS=2000`, `ROUTER_MAX_TOKENS=400`, `EXTRACTION_MAX_TOKENS=3000`, `JUDGE_SKIP_WHEN_CLEAN=true`.

## 4. Run it

```bash
./scripts/run_all.sh
# UI  http://localhost:5173
# API http://127.0.0.1:8000/docs
```

```bash
source .venv/bin/activate
ruff check api/ && python -m pytest api/tests/ -q
cd web && npm run build
```

Config: copy `api/.env.example → api/.env` (`LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`, `OCR_DPI`, Langfuse, `TEMPORAL_ENABLED`). Frontend `web/.env`: `VITE_API_BASE_URL=http://localhost:8000/api` (the `/api` suffix is required).
