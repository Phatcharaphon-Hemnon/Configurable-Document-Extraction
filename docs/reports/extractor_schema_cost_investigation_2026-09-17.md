# Extractor schema grammar-cost investigation — read-only findings (2026-09-17)

## 1. Structural cost drivers in `ExtractionResponseSchema` (ranked)

Measured serialized sizes (`_pydantic_to_json_schema`): router **620** chars, extractor **3757** chars, judge 1269. Drivers, ranked:

1. **`value: Optional[Union[str, float, int, list, dict]]`** (`llm_schemas.py:23`) — the prime suspect. Bare `list`/`dict` with no `items`/`properties` become open-ended recursive `anyOf` branches in GBNF. Public llama.cpp docs confirm this exact combination is pathological: the grammars README defaults objects to no-additional-properties because open objects are "slow and seems prone to hallucinations," documents "performance gotchas" (ggml-org/llama.cpp#4218), and constrains `anyOf`/`oneOf` handling (sources: `github.com/ggml-org/llama.cpp` grammars README; `github.com/ggerganov/llama.cpp/issues/4218`, `#7703`). This 5-way union (two branches unbounded-recursive) repeats **per field entry** inside `fields: list[…]`.
2. **`tables: list[ExtractedTable]`** (`documents.py:233-236`) — `rows: list[list[TableCell]]`, each `TableCell` carrying `confidence`, `source_span`, plus `evidence_refs: list[EvidenceReference]` (7-field object with tuple `box` + `Literal` role). Doubly-nested arrays of constrained objects multiply grammar states.
3. **`extra="forbid"` + `_patch_schema_for_strict_mode`** (`client.py:399-428`) forcing every property required with `additionalProperties: false` — rigidity cost, minor size cost.
4. Notably **absent**: the field catalog does **not** enter the wire schema — `fields` is a generic list; the catalog only shapes prompt text. So "many possible fields" is prompt cost, not grammar cost. The grammar cost is fixed structural overhead, which is good news for fixes.

## 2. Schema-splitting (multi-call) feasibility

- **Would require:** new narrower wire schemas (e.g. scalar-fields-only with `value: Optional[Union[str, float, int]]` — dropping the open `list`/`dict` branches — plus a separate tables call), and merge logic.
- **Would NOT require:** new infra for orchestration/budgeting — the region pipeline already does split → per-region extract with checkpoints → merge → reconcile (`_extract_page_with_regions`), with per-boundary dispatch budgets and `RegionPageOutcome` typed paths.
- **Existing seams helping:** merge/reconcile/validator/Judge all consume the full typed contract, so a split must rejoin before them — the region merge code is the template, but it currently assumes the *same* full schema per region (`region_requests.py` keeps the full wire schema by design), so per-kind narrowing is still unbuilt work with merge-correctness risk (a scalar living only inside a table band, etc. — as recorded in `compact_request_2026-09-16.md` §4).
- **Blocker if any:** none structural; it's engineering scope (new schemas + merge + tests), not a missing capability.

## 3. Dropping `strict:true` for extraction only

- **Structurally easy:** tier selection is per-call (`_strongest_tier`, `client.py:1241`); the `disable_strict_json_schema` setting already makes calls start at `json_object` (`client.py:1457`) with `plain` as final fallback, plus per-model rejected-tier memory. A per-stage variant (extractor starts at `json_object`, router/judge unchanged) is a small, additive change; result-cache fingerprint already includes `strict_schema` (`result_cache.py:201`), so cached results never cross modes.
- **What Validator would additionally handle: almost nothing new.** The client *always* parses into `ExtractionResponseSchema` regardless of tier, so provider-unvalidated output still faces pydantic validation; total garbage follows the existing classified fallback/correction paths. Span-less fields are already the validator's normal input (`ExtractedField.source_span` Optional; validator flags, never drops — `validator.py` evidence checks + `needs_review` on any actionable finding). The real cost is operational, not correctness: likely more parse-fallback cycles per page (more dispatches, more latency variance) — measurable via the existing dispatch ledger, not a silent-accuracy risk.

## 4. Recommended prototype (recommendation only, no implementation)

**Prototype the narrowed scalar schema first** (option 2 micro-version, not the full multi-call split): keep one call, keep `strict:true`, but replace the wire schema's `value: Optional[Union[str, float, int, list, dict]]` with `Optional[Union[str, float, int]]` and move tables to the existing `tables` block only (they're already separate). Reasoning: it directly removes cost driver #1 (the open recursive union branches) while changing neither call count, orchestration, validation, nor hallucination guarantees (`source_span`/`confidence` per field untouched); the isolation probe already proved the server completes fast on a small strict schema, so grammar size is the confirmed lever; and it's falsifiable in one bounded smoke run (completes ≤300s vs censored). If that still stalls, driver #2 (tables nesting) is next; multi-call split and strict-dropping stay as fallbacks with known costs.
