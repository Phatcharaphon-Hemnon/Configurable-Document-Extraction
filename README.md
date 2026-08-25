# Configurable Document Extraction

Extract structured data from scanned or photographed business documents
(invoices, purchase orders, delivery notes) with a multi-agent AI pipeline —
OCR for printed text, ICR (handwriting) via a vision model, strict JSON output,
hallucination guards, and full LLM observability.

## Features

- **Fixed 3 document types** — `invoice`, `purchase_order`, `delivery_note`
  (enforced end-to-end by Pydantic `Literal`).
- **Multi-agent pipeline** — Router → Extractor (one agent per type) →
  Validator → Judge.
- **OCR + ICR** — text stages run `nemotron-3.5-lightning-free` via the
  OpenCode Zen gateway; image uploads use a vision model (default `hy3-free`)
  or fall back to LlamaParse OCR. PDFs are OCR-split per page by LlamaParse.
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
./scripts/run_all.sh       # ONE command: API :8000 + Web :5173
```

First run creates `.venv`, installs dependencies and copies `.env` files automatically.

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
| `OPENCODE_API_KEY` | OpenCode Zen API key (free tier: `public`). |
| `LLAMA_CLOUD_API_KEY` | PDF OCR path (images skip it). |
| `ROUTER_MODEL_NAME` / `EXTRACTION_MODEL_NAME` / `JUDGE_MODEL_NAME` | Model per stage (default `nemotron-3.5-lightning-free`). |
| `VISION_MODEL_NAME` | Model for image uploads (default `hy3-free`; empty = LlamaParse OCR). |
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

## CI/CD

- `.github/workflows/ci.yml` — ruff lint, pytest, frontend build on every push/PR.
- `.github/workflows/sonarcloud.yml` — SonarQube Cloud static analysis
  (`sonar-project.properties`).

## Documentation

- `docs/architecture.md` — pipeline, security model, token strategy
- `docs/backend.md` — module map + API + KB layout
- `docs/frontend.md` — component structure + behaviour
- `docs/adr/` — architecture decision records
- `AGENTS.md` — persistent memory for AI coding agents
