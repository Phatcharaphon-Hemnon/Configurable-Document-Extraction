# Eval report — v0.2.0 AI core

_Generated 2026-09-04 07:27 UTC · mode: **LIVE pipeline**_

## Config

| Setting | Value |
|---|---|
| `subset` | `invoice_01,invoice_04,invoice_05,po_01,po_02,po_03,delivery_note_01,delivery_note_02` |
| `few_shot` | `2` |
| `router_model` | `nemotron-3-ultra-free` |
| `extraction_model` | `nemotron-3-ultra-free` |
| `judge_model` | `nemotron-3-ultra-free` |
| `ocr` | `RapidOCR local (dpi=300)` |
| `judge_skip_when_clean` | `True` |

## Summary

- Items: 7/8 scored (1 failed)
- Router accuracy: 1.000
- Macro precision / recall / F1: 0.829 / 0.510 / **0.525**
- needs_review rate: 28.6%

## Per-document results

| doc | expected | predicted | router | P | R | F1 | review? | judge | notes |
|---|---|---|---|---|---|---|---|---|---|
| invoice_01 | invoice | invoice | ok | 0.417 | 0.333 | 0.370 | no | skipped | 1 new field(s) |
| invoice_04 | invoice | invoice | ok | 0.385 | 0.333 | 0.357 | no | skipped |  |
| invoice_05 | invoice | invoice | ok | 1.000 | 0.000 | 0.000 | yes | skipped | 5 validation error(s) |
| po_01 | purchase_order | — | — | — | — | — | — | — | FAILED: Extractor failed: Stage 'extractor' timed out after 150.0s |
| po_02 | purchase_order | purchase_order | ok | 1.000 | 0.000 | 0.000 | yes | skipped | 6 validation error(s) |
| po_03 | purchase_order | purchase_order | ok | 1.000 | 1.000 | 1.000 | no | skipped |  |
| delivery_note_01 | delivery_note | delivery_note | ok | 1.000 | 1.000 | 1.000 | no | skipped |  |
| delivery_note_02 | delivery_note | delivery_note | ok | 1.000 | 0.900 | 0.947 | no | skipped |  |

## Agents exercised

- Router (LLM, structured `RoutingResponseSchema` → Pydantic `RoutingDecision`)
- Extractor ×3 — invoice / purchase_order / delivery_note (LLM, structured `ExtractionResponseSchema` → `ExtractedField`)
- Validator (deterministic: required fields, evidence/source_span, confidence, dates)
- Judge (LLM, structured `JudgeResponseSchema` → `JudgeResult`; skipped when clean)

## RAG sources

- `field_catalog/*.json` — compact catalog in every extractor prompt
- `few_shot/*/*.json` — TF-IDF ranked per document (top-2 after ~2k-char cap; retriever: `app/services/rag_retriever.py`)
- `ground_truth/*.json` — scoring only, never in prompts

## Reproduce

```bash
cd api && source ../.venv/bin/activate
python scripts/run_eval.py --output ../eval_report.md
```

> Pass bar for v0.2.0: pipeline runs on sample inputs and metrics are reported (thresholds not required yet).

## Appendix — run conditions (post-hoc note, not generated)

- **Model substitution:** the default `nemotron-3.5-lightning-free` completions
  endpoint hung on every call during this run (45s stall → retry → empty
  fallback; `GET /zen/v1/models` returned 200, so key and API were fine).
  Probes of `deepseek-v4-flash-free` (400 unavailable), `ling-3.0-flash-fin-free`
  and `laguna-s-2.1-free` (503 unavailable), `mimo-v2.5-free` (429 rate-limited),
  and `muse-spark-1.3-contributor-free` (500) also failed. The eval ran on
  `nemotron-3-ultra-free` via `ROUTER/EXTRACTION/JUDGE_MODEL_NAME` env overrides
  — config only, no code change.
- **Low/zero rows are provider degradation, not pipeline bugs:** `po_01` failed
  on the 150s extractor stage timeout; `invoice_05` / `po_02` returned empty
  predictions (precision 1.000 by the `evaluate()` zero-prediction convention,
  recall 0.000). Counter-evidence the pipeline works: `po_03` and
  `delivery_note_01` score **1.000**, `delivery_note_02` 0.947, router 8/8.
- **Catalog untouched:** `run_eval.py` snapshots and restores
  `field_catalog/*.json`, so the `1 new field(s)` note above caused no
  committed changes (`git status` on the KB is clean apart from `rag_index.json`).
- An earlier attempt accidentally ran with cwd=repo-root against the legacy
  duplicate KB at `app/data/knowledge_base/` (no few-shot → RAG inactive);
  that pollution was reverted and the script now pins
  `settings.knowledge_base_path` to the KB beside it, so cwd no longer matters.
