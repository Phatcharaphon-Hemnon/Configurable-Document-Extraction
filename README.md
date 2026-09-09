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

### Backend: `api/.env`

1. **Create it.** `./scripts/run_all.sh` creates `api/.env` from
   `api/.env.example` automatically (and warns if `LLM_API_KEY` is empty).
   Manual alternative: `cp api/.env.example api/.env`. Never commit this
   file — it holds live secrets and is git-ignored.
2. **Edit the 3 lines** that control the whole pipeline:
   ```bash
   LLM_PROVIDER=opencode
   LLM_API_KEY=public       # free-tier key; paste a real key to unlock everything
   LLM_MODEL=mimo-v2.5-free # any model ID of the active provider
   ```
   A standard cloud setup looks the same, e.g.:
   ```bash
   LLM_PROVIDER=ollama-cloud
   LLM_API_KEY=<paste key from https://ollama.com/settings/keys>
   LLM_MODEL=gpt-oss:20b
   ```
   Key sources for all providers: `docs/ai_provider.md`. `LLM_MODEL`
   accepts **any** model ID of the active provider (registry defaults are
   just fallbacks).
3. **Watch the three gotchas:** `deepseek` fails at startup without an
   explicit `LLM_MODEL` (it ships no default, by design); `openclaw`
   needs its gateway Chat Completions endpoint enabled first
   (`gateway.http.endpoints.chatCompletions.enabled: true`); `LLM_BASE_URL`
   overrides the automatic URL (e.g. Kimi China region
   `https://api.moonshot.cn/v1`).
4. **Restart the API** after any `.env` change — config is read at
   startup, and uvicorn must run from inside `api/` so `.env` resolves.
5. **Verify.** Fast offline check (from `api/`):
   ```bash
   ../.venv/bin/python -c "from app.core.config import Settings; s=Settings(); print(s.llm_provider, s.llm_base_url, s.llm_model)"
   ```
   Live check: `python scripts/time_gateway_modes.py`, or extract a
   document in the UI.

| Variable | Purpose |
|---|---|
| `LLM_PROVIDER` / `LLM_API_KEY` / `LLM_MODEL` | AI provider (11 options, see table below), the only secret, and the single text model for Router + Extractor + Judge. No native Claude entry — reach it via `openrouter`/`opencode`. |
| `OCR_DPI` | PDF render resolution for local RapidOCR (default `300`). |
| `SUPPORTED_LANGUAGES` | OCR languages (default `en,th`). |
| `ROUTER_MODEL_NAME` / `EXTRACTION_MODEL_NAME` / `JUDGE_MODEL_NAME` | Optional per-stage overrides (default: `LLM_MODEL`). |
| `FEW_SHOT_EXAMPLES_PER_DOC_TYPE` | Few-shot injection count (default 0 = cheapest). |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` | Optional tracing. |
| `TEMPORAL_ENABLED` | `false` (default) = in-process pipeline; `true` = Temporal workflow (run `python -m app.temporal.worker` from `api/`). |

### Frontend: `web/.env`

Created from `web/.env.example` by `run_all.sh` (or `cp` manually). The
only variable is the backend URL — local dev works with the file's empty
default (the Vite dev proxy forwards `/api` to `http://127.0.0.1:8000`
automatically); production sets e.g.
`VITE_API_BASE_URL=https://your-backend.onrender.com/api`. The `/api`
prefix is required. Never put secrets behind `VITE_` — it compiles into
the browser bundle (on Vercel, leave "Sensitive" unchecked).

### Provider base URLs (automatic per `LLM_PROVIDER`)

| `LLM_PROVIDER` | Base URL | Key env var |
|---|---|---|
| `openai` | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| `xai` | `https://api.x.ai/v1` | `XAI_API_KEY` |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai/` | `GEMINI_API_KEY` |
| `openrouter` | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` |
| `deepseek` | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` |
| `kimi` | `https://api.moonshot.ai/v1` | `MOONSHOT_API_KEY` |
| `ollama-cloud` | `https://ollama.com/v1` | `OLLAMA_API_KEY` |
| `ollama-local` | `http://localhost:11434/v1` | — (no key) |
| `mistral` | `https://api.mistral.ai/v1` | `MISTRAL_API_KEY` |
| `openclaw` | `http://127.0.0.1:18789/v1` | `OPENCLAW_API_KEY` (gateway token) |
| `opencode` | `https://opencode.ai/zen/v1` | `OPENCODE_API_KEY` (`public` = free tier) |

`LLM_API_KEY` falls back to the provider's native key var when set; either
one works. `LLM_BASE_URL` overrides the table (e.g. Kimi China region
`https://api.moonshot.cn/v1`). Full per-provider setup: `docs/ai_provider.md`.

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
ruff check api/ && python -m pytest api/tests/ -q   # lint + tests
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

## Multilingual page extraction release

All PDF pages retain their results and source previews, including mixed document types
and languages. Tables use their printed columns. History opens saved results without
starting a new extraction. See [page storage](docs/page_storage.md),
[multilingual OCR setup](docs/multilingual_ocr.md), [table contracts](docs/dynamic_tables.md)
and [evaluation instructions](docs/evaluation.md). Runtime history is now at
`data/extraction.db`; local Tesseract English/Thai dependencies must be available.
