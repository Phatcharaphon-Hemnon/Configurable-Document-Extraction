# Reference — architecture and module contracts

Current-state contracts for the pipeline, storage, and services. For dated
audit/eval outputs see `../reports/README.md`; for setup see
`../guides/README.md`.

| Doc | Covers |
|---|---|
| `architecture.md` | Pipeline, security model, token strategy |
| `backend.md` | Backend module map + API + KB layout |
| `frontend.md` / `frontend_helpers.md` | Component structure, behaviour, API client/hooks/utils |
| `api.md` | Endpoints and app wiring |
| `agents.md` | Router, Extractors, Validator (Judge: `judge_review.md`) |
| `judge_review.md` | Judge tolerance alignment + grouped UI |
| `services.md` | Orchestration, catalog, matching, dates |
| `schemas.md` / `extraction_response_contract.md` | LLM output contracts, response shape, Judge grounding |
| `database.md` / `page_storage.md` / `async_jobs.md` | SQLite persistence, source storage, async job flow |
| `dynamic_tables.md` / `line_items_evidence.md` | Source-language tables and per-cell evidence |
| `evidence_acceptance.md` | Evidence references & acceptance policy |
| `catalog_review.md` | Curating AI-discovered fields |
| `rag_kb.md` | RAG / knowledge-base retrieval |
| `openai_gpt.md` | LLM client (`api/app/services/client.py`) |
| `llm_request_queue.md` / `timeout_recovery.md` | Request queue, retries, timeout recovery |
| `provider_errors.md` | Redacted provider errors, surfaced end to end |
| `result_cache.md` | Persistent completed-result cache |
| `prompt_registry.md` | Versioned prompt templates (`api/app/prompts/`), content-derived cache invalidation |
| `ai_guardrails.md` | Guardrails implementation plan |
