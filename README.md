# Configurable Document Extraction

Extract structured data from scanned or photographed business documents
(invoices, purchase orders, delivery notes) with a multi-agent AI pipeline —
local OCR for printed text and handwriting, strict JSON output,
hallucination guards, and full LLM observability.

## Features

- **Fixed 3 document types** — `invoice`, `purchase_order`, `delivery_note`
  (enforced end-to-end by Pydantic `Literal`).
- **Multi-agent pipeline** — RapidOCR → Router → Extractor (one agent per
  type) → Validator → Judge.
- **Local OCR + single text model** — all uploads (images + PDFs) are OCR'd
  on-host with RapidOCR (ONNX, CPU, no API key, works offline);
  extraction uses one text model (`LLM_MODEL`, default `gpt-oss:20b` on
  Ollama Cloud). No vision model required.
- **Multi-document files** — one uploaded PDF can contain several documents;
  every page becomes its own extraction result.
- **Field catalog discipline** — field names match the catalog EXACTLY (no
  aliases/synonyms). Labeled values not in the catalog are added to it
  automatically (`source: ai_discovered`).
- **Token-minimized** — compact catalog prompts, few-shot OFF by default,
  reasoning disabled, text length caps.
- **Hallucination control** — every field carries `source_span` evidence +
  `confidence`; the Validator verifies evidence against the document.
- **Prompt-injection defense** — document text is sanitized before it reaches
  any LLM.
- **Observability** — Langfuse traces every agent call (auto-disabled without
  keys). Temporal workflow mode optional.
- **Evaluate tab** — score extractions against ground truth
  (precision / recall / F1 + mismatch table).

## Quick start

```bash
./scripts/run_all.sh       # ONE command: installs everything, then runs API :8000 + Web :5173
```

The script handles the FULL setup automatically: verifies prerequisites
(Python 3.11+, Node 18+), creates the `.venv`, installs API + web
dependencies, creates `.env` files from templates, and starts both servers.
Re-running skips everything already installed. Ctrl+C stops both.

- UI: http://localhost:5173
- API docs: http://127.0.0.1:8000/docs

## Project layout

```text
├── api/                        # FastAPI backend
│   ├── app/
│   │   ├── agents/            # router · 3 extractors · validator · judge
│   │   ├── api/routes.py      # /extract /templates /evaluate /jobs
│   │   ├── core/              # config (env) · security (injection guard)
│   │   ├── observability/     # Langfuse tracing
│   │   ├── schemas/           # Pydantic contracts (ExtractedField, ExtractionResult)
│   │   ├── services/          # orchestration · LLM client · field catalog · KB
│   │   ├── temporal/          # durable workflow (optional)
│   │   └── data/knowledge_base/  # field_catalog · few_shot · ground_truth · documents
│   ├── tests/                 # pytest
│   ├── .env.example
│   └── requirements.txt
├── web/                       # React + TypeScript + Vite frontend
│   └── src/{api,components,hooks,types,utils}
├── docs/                      # architecture.md · backend.md · frontend.md · adr/
├── skills/document-extraction/SKILL.md   # AI-agent skill file
├── AGENTS.md                  # AI-agent project memory
├── scripts/run_all.sh         # ONE command: API :8000 + Web :5173
└── .github/workflows/         # CI (ruff + pytest + build) · SonarCloud
```

## Configuration

Copy `api/.env.example` → `api/.env`:

| Variable | Purpose |
|---|---|
| `LLM_PROVIDER` / `LLM_API_KEY` / `LLM_MODEL` | AI provider (`openai` \| `ollama-cloud` \| `ollama-local`), the only secret, and the single text model for Router + Extractor + Judge (this branch: `ollama-cloud` + `gpt-oss:20b`). See `docs/ai_provider.md`. |
| `PADDLEOCR_LANG` | OCR language (`en` default, `th` for Thai documents). |
| `PADDLEOCR_USE_GPU` | `true` (default) = GPU with CPU fallback; `false` = force CPU. |
| `PADDLEOCR_DPI` | PDF render resolution (default `300`). |
| `ROUTER_MODEL_NAME` / `EXTRACTION_MODEL_NAME` / `JUDGE_MODEL_NAME` | Optional per-stage overrides (default: `LLM_MODEL`). |
| `FEW_SHOT_EXAMPLES_PER_DOC_TYPE` | Few-shot injection count (default 0 = cheapest). |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` | Optional tracing. |
| `TEMPORAL_ENABLED` | `false` (default) = in-process pipeline; `true` = Temporal workflow (run `python -m app.temporal.worker` from `api/`). |

Frontend: `web/.env` → `VITE_API_BASE_URL=http://localhost:8000/api`
(the `/api` prefix is required).

## API contract

Core result shape (one per document/page):

```json
{
  "doc_type": "invoice",
  "fields": [
    {"name": "invoice_number", "value": "INV-001", "confidence": 0.95,
     "source_span": "Invoice No: INV-001", "is_new_field": false}
  ],
  "validation_errors": [],
  "needs_review": false,
  "completeness_score": 1.0,
  "judge": {"score": 0.9, "issues": [], "notes": "ok"}
}
```

## Development

```bash
source .venv/bin/activate
ruff check backend/ && python -m pytest api/tests/ -q   # lint + tests
cd web && npm run build                                # typecheck + build
```

## Deployment (Vercel + hosted backend)

The web app deploys to Vercel; the FastAPI backend needs a long-running host
(Render, Railway, Fly.io — not Vercel serverless).

**Web (Vercel):**
1. Import the repo, set **Root Directory** to `web`.
2. Environment variable: `VITE_API_BASE_URL` = your backend URL + `/api`
   (e.g. `https://your-backend.onrender.com/api`).
   > **Important:** leave the **"Sensitive" checkbox UNCHECKED**. `VITE_`
   > variables are compiled into the browser bundle — Vercel rejects them as
   > Sensitive with *"Remove the public framework prefix…"*. The URL contains
   > no secrets, so a normal variable is correct.
3. Never put API keys (`LLM_API_KEY`, Langfuse
    keys) in the web project — anything prefixed `VITE_` is public. Keys belong
    in the backend host's environment. (OCR needs no key — RapidOCR runs locally.)

**Backend (Render/Railway):**
- Start command: `pip install -r api/requirements.txt && cd api && uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Set `LLM_PROVIDER` / `LLM_API_KEY` / `LLM_MODEL` (+ optional Langfuse keys). No OCR key needed.
- RapidOCR runs on CPU (~1 s/page); no GPU setup required.
- CORS: the backend already allows `*.vercel.app` previews via
  `allow_origin_regex`; add your production domain to `FRONTEND_ORIGINS`.

**Local dev without any .env:** `web/.env` is optional — the Vite dev server
proxies `/api` to `http://127.0.0.1:8000` automatically.

## CI/CD

- `.github/workflows/ci.yml` — ruff lint, pytest, frontend build on every push/PR.
- `.github/workflows/sonarcloud.yml` — SonarQube Cloud static analysis
  (`sonar-project.properties`).

## Documentation

- `CHANGELOG.md` — what changed, newest first
- `docs/architecture.md` — pipeline, security model, token strategy
- `docs/backend.md` — module map + API + KB layout
- `docs/frontend.md` — component structure + behaviour
- `docs/local_ocr.md` — local RapidOCR pipeline notes
- `docs/langfuse_tracing.md` — Langfuse v4 trace design + audit
- `docs/async_jobs.md` — async upload/poll job flow
- `docs/adr/` — architecture decision records
- `AGENTS.md` — persistent memory for AI coding agents
