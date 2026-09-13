# Hallucination Audit — Extraction Pipeline (2026-09-06)

Scope: places where an unsupported, fabricated, or unverifiable field value
could reach the final `ExtractionResult`. Read-only audit — no code changed.
All findings verified against actual code (file:line references below).

Pipeline under audit: `RapidOCR → Router → Extractor → Validator → Judge`
(`api/app/services/extraction_service.py::_extract_one_page`), plus the
Temporal mirror path (`api/app/temporal/activities.py`, `workflows.py`).

## Issue 1: source_span is prompt-level advice only, never schema-enforced
- Location: `api/app/agents/extractors.py:_COMMON_RULES` (L38-39); `api/app/schemas/llm_schemas.py:ExtractedFieldEntry` (L16-22)
- Root cause: the prompt says every field MUST include `source_span`, but the structured-output schema declares `source_span: Optional[str] = None` with default `confidence 0.5`. Even the strict-mode patcher (`sut_genai_client.py:_patch_schema_for_strict_mode`, L101-129) only forces the key to be present, not non-null — `null` validates fine. The extractor (`extractors.py`, L154-159) passes `entry.source_span` straight through, so a span-less field is constructed without error and only flagged later (as review, not rejection).
- Trigger example: document text `"INVOICE #A123 Total 250 THB"`, LLM returns `{"fields": [{"name": "tax_amount", "value": "17.5", "confidence": 0.9, "source_span": null}]}` → parses OK, `ExtractedField(name=tax_amount, value=17.5, confidence=0.9, source_span=None)` reaches the validator.
- Severity: medium
- Proposed fix: make `source_span` a required non-empty string in `ExtractedFieldEntry` (or drop span-less fields in `BaseExtractor.extract`).

## Issue 2: plain-prompt fallback tier drops the document and the rules
- Location: `api/app/services/sut_genai_client.py:generate_structured` (L316-345, Attempt 3)
- Root cause: when Attempts 1 (json_schema) and 2 (json_object) fail to parse, Attempt 3 repairs with the prompt `"Return ONLY the corrected, valid JSON object ... Response to fix: <raw_text>"` — the original document text, catalog, and `_COMMON_RULES` (including the never-guess / source_span rules) are NOT included in that repair call. The model reconstructs fields from its own broken output with no grounding, and `_try_parse` (L806+) additionally accepts the first `{...}` block out of surrounding prose.
- Trigger example: Attempt 1 returns malformed `"... total_amount: 999 ..."` (unparseable); Attempt 3 receives only that fragment, no document text, and returns `{"fields": [{"name": "total_amount", "value": 999, "confidence": 0.95, "source_span": "Total 999"}]}` → accepted as a successful extraction.
- Severity: high
- Proposed fix: include the original prompt (document text plus rules) in the Attempt 3 repair call.

## Issue 3: "Invent a name" rule encourages fabricated new fields
- Location: `api/app/agents/extractors.py:_COMMON_RULES` (L33-37)
- Root cause: the prompt orders the model to never drop a clearly labeled value and to invent a snake_case name (e.g. label "Loyalty Earned" → `loyalty_earned`) with `"new_field": true`. The pipeline recomputes `is_new_field` authoritatively (`extractors.py` L159; `field_catalog.py:register_discovered_fields` L131-137), so the flag itself is safe — but there is no check that the "label" actually exists in the document, so a hallucinated label plus invented name flows into catalog registration (see Issue 9).
- Trigger example: document `"INVOICE Total 100"` (no loyalty program), LLM returns `{"name": "loyalty_earned", "value": "10 points", "confidence": 0.88, "source_span": "Loyalty Earned 10 points", "new_field": true}` → marked `is_new_field=True` and registered if it passes the weak gates.
- Severity: medium
- Proposed fix: require the invented name's source_span to be a verbatim document substring before registration.

## Issue 4: token-overlap check passes scattered / generic / short spans
- Location: `api/app/core/security.py:check_evidence` (L89-100)
- Root cause: evidence passes on substring match OR unordered set-token overlap ≥ 0.75. Sets discard order, position, and duplicates, so (a) a value assembled from words scattered across the document passes; (b) a single-token generic span (`"Total"`, `"receipt"`, `"THB"`) passes whenever that word appears anywhere; (c) a two-token span passes on any 2-of-2 co-occurrence even if the pairing is wrong (e.g. value `1900` quoted as `"Total 1900"` when the document says `"Total 250 ... 1900"` elsewhere).
- Trigger example: document `"Total 250 THB. Page 1900 of catalog."`, field `total_amount=1900` with `source_span="Total 1900"` → normalized span not a substring, but tokens `{total, 1900}` ⊆ document tokens → 2/2 = 1.0 ≥ 0.75 → returns None (passes).
- Severity: high
- Proposed fix: require the span to be a contiguous normalized substring (or n-gram containment) for short spans, keeping token overlap only as a narrow OCR-noise fallback.

## Issue 5: empty document text disables evidence checking entirely
- Location: `api/app/core/security.py:check_evidence` (L89-101)
- Root cause: the span-vs-document comparison runs only `if document_text:` — when OCR yields empty/None text (blank scan, OCR failure, `parse_activity` returning `[""]` on exception in `temporal/activities.py` L36-41), the function falls through to `return None` (pass) for every field that has any non-empty span. The Temporal `validate_activity` passes `page_text or None`, so the same hole applies there.
- Trigger example: `document_text=""` (failed OCR), LLM returns `total_amount=5000, source_span="Total 5000", confidence=0.9` → `check_evidence` returns None → no validation error from the evidence guard.
- Severity: high
- Proposed fix: return a "no document text to verify against" problem when `document_text` is empty and any field carries a non-null value.

## Issue 6: is_image_extraction=True unconditional-trust bypass is live API
- Location: `api/app/core/security.py:check_evidence` (L85-86); callers `api/app/agents/validator.py:ValidatorAgent.validate` (L42, L65-71), `api/app/services/extraction_service.py` (L448, `is_image_extraction=False`)
- Root cause: with `is_image_extraction=True`, any non-empty `source_span` returns None with zero verification. The current in-process pipeline always passes False, and `BaseExtractor.extract` no longer receives image bytes from the service (`extraction_service.py` calls `extract(text=..., few_shot=...)` only), so the bypass is NOT reachable on the main path today — but it remains reachable via any direct `ValidatorAgent.validate(..., is_image_extraction=True)` caller, and the schema still advertises `ExtractionResult.extraction_source="vision"`, so a future or third-party caller can silently disable the guard.
- Trigger example: `validator.validate(doc_type="invoice", fields=[ExtractedField(name="total_amount", value=9999.0, confidence=0.99, source_span="whatever")], document_text="INVOICE Total 10", is_image_extraction=True)` → returns no errors, `needs_review=False`.
- Severity: low
- Proposed fix: remove the bypass parameter (or verify spans against OCR text even for image extractions) since the pipeline is text-only.

## Issue 7: validator checks the serialized blob, not array rows
- Location: `api/app/agents/extractors.py:_coerce_value` (L71-80); `api/app/agents/validator.py:ValidatorAgent.validate` (L63-73)
- Root cause: list/dict values (e.g. `line_items`) are JSON-serialized into a single string by `_coerce_value`, and `check_evidence` then validates one combined `source_span` against the blob. A single span covering a few real rows passes the whole array, so hallucinated extra rows (wrong quantity, phantom drink/dessert line) inside the JSON are never individually verified — despite `_COMMON_RULES` (L44-48) demanding every row.
- Trigger example: document has 2 item rows; LLM returns `line_items` with 4 rows (2 phantom, `total_price` invented) plus `source_span` quoting the 2 real rows → token overlap passes → no evidence error for the phantom rows.
- Severity: high
- Proposed fix: validate array fields per element (require per-row spans) instead of one span for the serialized blob.

## Issue 8: judge_skip_when_clean can launder a confidently-wrong result
- Location: `api/app/services/extraction_service.py:_extract_one_page` (L464-476); `api/app/core/config.py:Settings` (Ljudge_skip_when_clean / Ljudge_skip_confidence, default true / 0.85)
- Root cause: the Judge LLM stage is skipped when there are no validation errors, completeness is 1.0, and every field confidence ≥ 0.85. A confidently-wrong hallucination that survives the weak evidence check (Issues 4-5, e.g. misattributed `source_span` with confidence 0.9) therefore skips the only independent LLM sanity check and lands in the final `ExtractionResult` with `needs_review=False`. The Temporal workflow (`workflows.py`) always runs `judge_activity`, so the two paths disagree on this safety net. Additionally the judge prompt (`judge.py` L30-44) shows only `{name: value}` plus source text — never the `source_span`s — so even when it runs it cannot verify provenance.
- Trigger example: `total_amount=1900` (true 250), `confidence=0.92`, `source_span="Total 1900"` passing token overlap (Issue 4), all required fields present → `skip_judge=True` → final result `needs_review=False`, no judge score.
- Severity: high
- Proposed fix: never skip the judge when any field value is numeric/monetary, or include source_spans in the judge prompt so it can check provenance.

## Issue 9: hallucinated names are permanently written to the catalog
- Location: `api/app/services/field_catalog.py:is_registerable_new_field` (L91-98), `skip_reason` (L101-109), `register_discovered_fields` (L131-156)
- Root cause: the registration gate checks only placeholder-value, sane snake_case (`_SANE_NAME_RE`), and confidence ≥ 0.6 (`NEW_FIELD_MIN_CONFIDENCE`, env-overridable). It never calls `check_evidence`, so a hallucinated field with a confident non-placeholder value and a plausible snake_case name is appended to the catalog JSON (`source="ai_discovered"`, Ltype string) and becomes "known" for all future documents — compounding the original hallucination. The 0.6 bar equals the validator's low-confidence boundary, so a field at exactly 0.6 passes both.
- Trigger example: Issue 3's phantom `loyalty_earned="10 points"`, confidence 0.88 → `skip_reason` returns None → `catalog.add_fields("invoice", ["loyalty_earned"])` persists it to `invoice_fields.json`.
- Severity: high
- Proposed fix: require evidence verification (`check_evidence` clean) plus a higher confidence bar before auto-registering a new field.

## Issue 10: validation flags but never blocks — bad values ship with a flag
- Location: `api/app/agents/validator.py:ValidatorAgent.validate` (L95-99); `api/app/services/extraction_service.py` (Lfinal `ExtractionResult` assembly)
- Root cause: every guard in the validator (evidence, confidence < 0.6 at L77-78, unparseable dates at L83-87) only appends strings to `validation_errors` and sets `needs_review=True`; the offending fields stay in the returned `fields` list verbatim. Downstream consumers that ignore `needs_review` receive fabricated values as normal output. Boundary detail: the confidence check is strict `< 0.6`, so a field at exactly 0.60 with a bad-but-passing span raises no confidence error (only the evidence error, if the span fails). Date checking applies only to catalog-typed `date` fields, so AI-discovered fields (typed `string` at registration) with garbage dates pass.
- Trigger example: `invoice_date="32/13/2025"` as an AI-discovered string field, or any flagged `total_amount`, remains in `ExtractionResult.fields` with `needs_review=True` — a caller reading `.fields` directly consumes the hallucination.
- Severity: medium
- Proposed fix: strip or null fields with failed evidence checks from the returned list (keeping them in an audit/quarantine field) instead of shipping them alongside the flag.
