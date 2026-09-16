# RAG / Knowledge-Base Retrieval (v0.2.0)

Offline, dependency-free retrieval over the project knowledge base.
No embeddings, no vector DB, no network — TF-IDF cosine over tokens
(stdlib `re` + `math` + `collections` only).

## Sources (RAG inventory)

| Source | Path | Used for |
|---|---|---|
| Field catalog | `api/app/data/knowledge_base/field_catalog/*.json` | Compact prompt block (name + type + required, one line each) — every extractor call |
| Few-shot examples | `api/app/data/knowledge_base/few_shot/{invoice,po,delivery_note}/*.json` (5 per type) | Ranked per query, top-N injected as pattern guidance (only when `FEW_SHOT_EXAMPLES_PER_DOC_TYPE > 0`) |
| Ground truth | `api/app/data/knowledge_base/ground_truth/*.json` (20) | Auto-eval + `run_eval.py` scoring — never injected into prompts |
| Sample PDFs | `api/app/data/knowledge_base/documents/*.pdf` (20) | Eval inputs only |

## How retrieval works

1. `api/scripts/ingest_kb.py` walks the KB and writes `rag_index.json`
   (per-example token counts + corpus document frequencies + inventory meta).
   Re-run after adding examples; the live path does not require the file.
2. `app/services/rag_retriever.py::rank_examples(examples, query, limit)`
   scores each candidate's `input_text` (+ `description`) against the
   incoming page's OCR text with TF-IDF cosine. Ties / empty queries fall
   back to sorted-file order (legacy first-N behaviour).
3. Wiring: `extraction_service.py::_extract_one_page` passes the page text
   as `query` into `knowledge_base.py::get_few_shot_examples(..., query=...)`;
   the existing ~2k-char budget cap (`_cap_by_size`) still applies after
   ranking, so token cost is unchanged.

## Why TF-IDF and not embeddings

- Offline requirement: the pipeline must run without extra services or
  downloads (RapidOCR models aside).
- 15 examples: ranking 15 short texts is microseconds; an embedding model
  would add hundreds of MB for no measurable gain at this scale.
- Upgrade path: if the KB grows past ~100 examples per type, swap the
  scorer for embeddings behind the same `rank_examples` signature.

## Verification

- `api/tests/test_rag_retriever.py` — ranking prefers lexically similar
  examples, empty query keeps stable order, limits respected, ingest writes
  a valid index.
- `python scripts/ingest_kb.py` — prints the source inventory; exit 0.
- Eval parity: `run_eval.py --few-shot 0` vs `--few-shot 2` shows whether
  retrieved examples move F1 on the gold subset.
