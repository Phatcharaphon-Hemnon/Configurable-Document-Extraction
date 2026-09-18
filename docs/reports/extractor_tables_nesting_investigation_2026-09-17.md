# Extractor tables-nesting investigation — read-only findings (2026-09-17)

## 1. Tables' grammar contribution (structural)

Wire nesting for one cell's evidence (`documents.py:222-236`, confirmed in the emitted schema `$defs` = `EvidenceReference, ExtractedFieldEntry, ExtractedTable, TableCell, TableColumn`):
`root → tables[] → ExtractedTable{name, columns[], rows} → rows[][] → TableCell{column, value, confidence, source_span, evidence_refs[], acceptance} → EvidenceReference{block_id, page_number, subspan, box(tuple), engine, role(Literal), …}` — ~6 levels of array/object nesting with two `$ref` hops, every level `additionalProperties: false` + all-properties-required via `_patch_schema_for_strict_mode` (`client.py:399-428`), which additionally promotes backend-defaulted fields (`evidence_refs`, `acceptance`, TableCell `confidence`/`source_span`) into mandatory grammar branches.

Public sources distinguishing nesting-depth from size (cited, not speculated):
- `github.com/ggml-org/llama.cpp` grammars README: "Grammars currently have performance gotchas (see #4218)"; "Nested `$ref`s are broken (#8073)" — our schema nests `$ref`s three deep.
- Issue #4218 (`speed-up grammar sampling`): "inference gets at some point **exponentially slower when there are a lot of deeply nested, but open grammars**"; "when the model outputs a **list of nested subobjects** this effect comes when the list is long"; "grammar stacks can go up 800K"; suspected "exponential in the length of the parsed string."
- The JSONSchemaBench paper (arXiv 2501.10868): schema features like arrays/enums take some backends "40 seconds to 10 minutes to process" — cost driver is structural, not char count (consistent with our 3757→3674 shrinkage changing nothing).

## 2. Real-world frequency: tables are effectively absent

- 20/20 manifest pages: `tables: []`. 9/9 History payloads: `tables: []`.
- Catalogs define `line_items` (invoice + PO), so tables are representable and plausibly necessary for real invoices — but **zero extracted tables exist in any measured data**. Tables are rare-or-never in practice, yet every extractor call pays their full grammar cost. (Caveat: SROIE annotations only cover company/address/date/total, so absence there is scope, not proof of real-world rarity.)

## 3. Isolation feasibility

- **Conditional second call** needs a "table likely" signal that the full-page path doesn't currently emit (router doesn't detect tables). But a seam exists: `regions.py` already detects table bands geometrically (`_is_table_line`, `kinds` incl. `"table"`, `MAX_TABLE_ROWS_PER_GROUP = 8`) — reusable as a gate without the full region pipeline, though wiring OCR-geometry into the full-page path is new plumbing.
- **Cheaper alternatives within one call:** (a) wire schema with `tables` omitted entirely (fields-only strict call); tables covered later or never; (b) prune model-unauthored fields from the wire contract (`evidence_refs`, `acceptance` are backend-resolved per `EvidenceReference` docs — the model needn't emit them); (c) `maxItems` caps on rows (GBNF supports `maxItems` per the README example).
- What splitting would NOT require: new orchestration/budget infra (region merge + dispatch budgets exist); what it WOULD require: new schema types + join logic + cache-fingerprint separation (already fingerprinted pattern at `result_cache.py:201`).

## 4. Recommended next falsification (smallest possible)

**Fields-only strict schema, one call, same 300s ceiling**: drop the `tables` block from the wire schema (keep everything else byte-identical, including the narrowed value union). If X51008142033 completes ≤300s, driver #2 is confirmed and the conditional-tables design earns further work; if still censored, nesting is exonerated and attention moves to strict-mode itself (extractor-json_object probe) or server-side config. One-line-schema-scope change, falsifiable in a single bounded smoke — recommendation only, no implementation.
