# Hallucination Fixes — 2026-09-06

> Follow-up to `docs/hallucination_audit_2026-09-06.md` (10 issues, read-only
> audit). 9 fixed, 1 deferred. Machine-readable manifest:
> `docs/hallucination_fixes_manifest_2026-09-06.json`. Last updated: 2026-09-06.

## 1. Summary

The audited risk class is **ungrounded field values reaching the final
`ExtractionResult`**: every layer of the pipeline treated evidence as advice
rather than enforcement — the prompt demanded a `source_span` per field but
the schema accepted `null`, the repair fallback rebuilt JSON with no document
in context, the deterministic evidence check passed scattered words and waved
through empty OCR text (plus a live unconditional-trust bypass for image
extractions), array fields were verified as one serialized blob instead of
per row, the Judge safety net was skipped exactly when a confident hallucination
looked cleanest, invented field names were permanently written to the field
catalog, and the validator flagged bad values without ever removing them. The
fixes below move each of those checks from prompt-level suggestion to
schema-or-code enforcement, all in the safe direction (flag + `needs_review`,
never silent drop) — except Issue 10, which is deferred because stripping
fields needs a quarantine contract change.

## 2. Findings

### Issue 1: source_span was prompt advice, never schema-enforced (medium)

Root cause: `_COMMON_RULES` in `api/app/agents/extractors.py` (L38-39) says
every field MUST include `source_span`, but `ExtractedFieldEntry` in
`api/app/schemas/llm_schemas.py` (L16-22) declared
`source_span: Optional[str] = None` — and even the strict-mode patcher
(`sut_genai_client.py::_patch_schema_for_strict_mode`) only forces the key
present, not non-null. The extractor passed `entry.source_span` straight
through, so e.g. `tax_amount=17.5, confidence=0.9, source_span=null`
constructed fine and only earned a review flag downstream.

Before → after: span-less LLM output parsed OK and flowed downstream; now
`source_span` is a required non-empty string (`min_length=1`), so span-less
output fails parsing and triggers the client's retry tiers instead. The
output contract `ExtractedField.source_span` stays `Optional` — the validator
still flags (never drops) span-less fields with `needs_review=True`.

Test: `api/tests/test_schemas.py::test_issue1_spanless_entry_rejected_by_schema`

### Issue 2: plain-prompt fallback repaired JSON with no document in context (high)

Root cause: in `api/app/services/sut_genai_client.py::generate_structured`
(L316-345), when Attempts 1 (`json_schema`) and 2 (`json_object`) fail to
parse, Attempt 3 repaired with `"Return ONLY the corrected, valid JSON
object ... Response to fix: <raw_text>"` — the document text, catalog, and
`_COMMON_RULES` (never-guess / source_span rules) were NOT included, and
`_try_parse` additionally accepts the first `{...}` block out of prose. The
model reconstructed fields from its own broken fragment with no grounding.

Before → after: a malformed fragment like `"... total_amount: 999 ..."` was
repaired into `total_amount=999, confidence=0.95` ungrounded; now the Attempt 3
prompt re-embeds the original extraction request (document text plus rules)
alongside the broken fragment and schema, so the repair stays grounded.

Test: `api/tests/test_sut_genai_fallback_tiers.py::test_issue2_fallback_repair_keeps_document_and_rules`

### Issue 3: "invent a name" rule could mint fabricated fields (medium)

Root cause: `_COMMON_RULES` in `api/app/agents/extractors.py` (L33-37) orders
the model to never drop a clearly labeled value and to invent a snake_case
name (`"Loyalty Earned"` → `loyalty_earned`, `"new_field": true`). The
`is_new_field` flag itself is recomputed authoritatively against the catalog,
so the flag was safe — but nothing checked that the "label" exists in the
document, so a hallucinated label plus invented name flowed toward catalog
registration (see Issue 9).

Before → after: an invented name with a confident non-placeholder value could
register; now it only registers when its `source_span` verifies against the
document text via `check_evidence` — closed by the shared Issue 9 evidence
gate (`skip_reason`), which refuses invented-but-unverifiable names with an
evidence reason.

Test: `api/tests/test_field_catalog.py::test_issue3_invented_name_without_label_not_registered`

### Issue 4: token-overlap check passed scattered / generic / short spans (high)

Root cause: `check_evidence` in `api/app/core/security.py` (L89-100) passed on
substring match OR unordered set-token overlap ≥ 0.75. Sets discard order, so
a value assembled from scattered words passed (document `"Total 250 THB.
Page 1900 of catalog."`, field `total_amount=1900` with span `"Total 1900"` →
tokens `{total, 1900}` ⊆ document tokens → 1.0 ≥ 0.75 → pass), as did
single-token generic spans (`"Total"`, `"THB"`) appearing anywhere.

Before → after: any-length overlap fallback; now short spans (≤ 3 tokens) must
be a contiguous normalized substring with NO overlap fallback, while longer
spans keep the 0.75 overlap fallback as a narrow OCR-noise tolerance.

Test: `api/tests/test_security.py::test_issue4_scattered_span_rejected`

### Issue 5: empty document text disabled evidence checking entirely (high)

Root cause: the span-vs-document comparison in `check_evidence` ran only
`if document_text:` — on blank scans / OCR failures (empty or `None` text,
including the Temporal `parse_activity` exception path returning `[""]` and
`validate_activity` passing `page_text or None`), every field with a non-empty
span fell through to `return None` (pass).

Before → after: `total_amount=5000, source_span="Total 5000"` on empty OCR
text passed silently; now `check_evidence` returns a `"no document text to
verify against (possible hallucination)"` problem whenever the text is
empty/`None` and the field carries a value.

Test: `api/tests/test_security.py::test_issue5_empty_document_text_flags`

### Issue 6: is_image_extraction unconditional-trust bypass (low)

Root cause: `check_evidence` early-returned `None` for any non-empty span when
`is_image_extraction=True`. Not reachable on the main path today (the
in-process pipeline always passes `False`, and `BaseExtractor.extract` no
longer receives image bytes), but reachable via any direct
`ValidatorAgent.validate(..., is_image_extraction=True)` caller — and the
schema still advertises `extraction_source="vision"`, so a future caller
could silently disable the guard.

Before → after: any span trusted with zero verification under the flag; now
the bypass is removed — spans are always verified against OCR text when text
exists (parameter kept for caller compatibility), and the empty-text image
case is covered by the Issue 5 flag.

Test: `api/tests/test_security.py::test_issue6_image_bypass_still_verifies`

### Issue 7: validator checked the serialized blob, not array rows (high)

Root cause: list/dict values (e.g. `line_items`) are JSON-serialized into one
string by `_coerce_value` (`extractors.py` L71-80), and `check_evidence` then
validated one combined `source_span` against the blob — one span quoting a few
real rows passed the whole array, so phantom rows (wrong quantity, invented
`total_price`) inside the JSON were never individually verified, despite
`_COMMON_RULES` (L44-48) demanding every row.

Before → after: 2 real rows quoted → 4 returned rows (2 phantom) passed; now
`_check_array_rows` in `api/app/agents/validator.py` parses JSON-serialized
array values and verifies each row's text against the document (substring or
0.75 token overlap) — the first unverifiable row yields a validation error +
`needs_review`. The field itself is never dropped.

Test: `api/tests/test_pipeline.py::test_issue7_phantom_array_rows_flagged`

### Issue 8: judge_skip_when_clean could launder a confidently-wrong result (high)

Root cause: in `extraction_service.py::_extract_one_page` (L464-476) the Judge
LLM stage is skipped when there are no validation errors, completeness is
1.0, and every confidence ≥ 0.85 — so a confidently-wrong hallucination that
survived the weak evidence check (e.g. `total_amount=1900` true 250,
confidence 0.92, overlapping-but-wrong span) skipped the only independent LLM
sanity check and landed with `needs_review=False`. The Temporal workflow
always runs `judge_activity`, so the paths disagreed — and the judge prompt
(`judge.py` L30-44) showed only `{name: value}`, never `source_span`s, so even
a running judge could not verify provenance.

Before → after: clean-looking confident numbers skipped review; now the skip
additionally requires every numeric field's `source_span` to be a contiguous
substring of the page text (`_all_numeric_spans_verbatim`) — non-verbatim
numeric spans always get a judge review — and the judge prompt carries a
per-field provenance block (`value` + `source_span`) so it can check grounding.

Test: `api/tests/test_pipeline.py::test_issue8_judge_not_skipped_for_nonverbatim_numeric_span`

### Issue 9: hallucinated names permanently written to the catalog (high)

Root cause: the registration gate (`field_catalog.py:is_registerable_new_field`,
`skip_reason`, `register_discovered_fields`) checked only placeholder-value,
sane snake_case, and confidence ≥ 0.6 (`NEW_FIELD_MIN_CONFIDENCE`) — it never
called `check_evidence`, so Issue 3's phantom `loyalty_earned="10 points"`
(confidence 0.88) was appended to `invoice_fields.json` (`source="ai_discovered"`)
and became "known" for all future documents, compounding the hallucination.
The 0.6 bar equaled the validator's low-confidence boundary, so a field at
exactly 0.6 passed both.

Before → after: confident plausible-snake_case hallucinations persisted; now
registration requires `check_evidence` clean when document text is provided
(both production paths — in-process service and Temporal `extract_activity` —
pass the page text) and the effective bar is `max(configured, 0.8)` via
`NEW_FIELD_CONFIDENCE_FLOOR`. Refusals are reported in `skipped` with reasons,
never silent.

Test: `api/tests/test_field_catalog.py::test_issue9_hallucinated_new_field_not_registered`

### Issue 10: validation flags but never blocks (medium) — DEFERRED

Root cause: every validator guard (evidence, confidence < 0.6, unparseable
dates) only appends to `validation_errors` and sets `needs_review=True`; the
offending fields stay in `fields` verbatim, so downstream consumers that
ignore `needs_review` consume fabricated values as normal output.

Status: deferred by design, no code changed. The proper fix needs an
`ExtractionResult` quarantine/audit field (schema + service + Temporal +
frontend contract change) so rejected values stop shipping in `.fields`
without being silently dropped. Current flag behavior retained.

Test: n/a (deferred)

## 3. Changes made

- `api/app/schemas/llm_schemas.py` — `ExtractedFieldEntry.source_span` is now
  a required non-empty string (`min_length=1`); span-less LLM output fails
  parsing and retries instead of flowing downstream (Issue 1).
- `api/app/services/sut_genai_client.py` — Attempt 3 repair prompt re-embeds
  the original extraction request (document text plus rules) with the broken
  fragment and schema (Issue 2).
- `api/app/core/security.py` — `check_evidence`: short spans (≤ 3 tokens) must
  be contiguous normalized substrings (no overlap fallback); empty/`None`
  document text returns a "no document text to verify against" problem instead
  of passing; the `is_image_extraction` trust bypass is removed (parameter
  kept for compatibility) (Issues 4, 5, 6).
- `api/app/agents/validator.py` — new `_check_array_rows` per-row evidence
  check for JSON-serialized array fields; first unverifiable row errors +
  `needs_review`, field never dropped (Issue 7).
- `api/app/services/extraction_service.py` — judge skip additionally requires
  `_all_numeric_spans_verbatim` (every numeric field's span a contiguous
  page-text substring); catalog writes go through the shared
  `register_discovered_fields` helper with page text (Issues 8, 9).
- `api/app/agents/judge.py` — prompt now includes per-field `source_span`
  provenance so the judge verifies grounding, not just values (Issue 8).
- `api/app/services/field_catalog.py` — `skip_reason`/`register_discovered_fields`:
  evidence gate (`check_evidence` clean when text provided) + effective
  confidence floor `max(configured, 0.8)`; skips logged with reasons (Issues
  3, 9). `api/app/temporal/activities.py::extract_activity` uses the same
  helper with page text so both paths agree (Issue 9).
- Tests — 9 new regression tests (one per fixed issue, see section 2);
  `api/tests/test_guards.py` image-bypass tests and
  `api/tests/test_sut_genai_client_retry.py` no-resend test updated to the new
  intended behavior (trust removed; repair MUST resend the prompt).
- Issue 10: no change (deferred — needs quarantine contract).

## 4. What this does NOT fix

- **Heuristic evidence checking can still be fooled by a fabricated span using
  real document vocabulary.** A span that IS a contiguous document substring
  (or a long span with ≥ 0.75 token overlap) passes even when the paired value
  is wrong — e.g. quoting a real `"Total 250"` sentence as the span for
  `total_amount=2500`, or copying any genuine line as cover for an invented
  number. The check proves the words exist, not that the value follows from them.
- **Long-span overlap fallback stays unordered.** Kept deliberately as OCR-noise
  tolerance, so scattered-word assembly can still pass for spans over 3 tokens.
- **Per-row array verification uses the same heuristic**, so a phantom row
  paraphrased in document vocabulary can pass the same way.
- **The judge is still an LLM** — provenance in its prompt helps, but it can
  miss or itself hallucinate; and non-numeric confidently-wrong fields with
  verbatim spans can still skip it via `judge_skip_when_clean`.
- **Issue 10 is open**: flagged values still ship in `.fields` with
  `needs_review=True`. Consumers that ignore the flag consume hallucinations.
- **OCR errors cut the other way**: a true span mangled by OCR fails
  verification and lands in `needs_review` — the safe direction, but expect
  review load on noisy scans.

## 5. Verification

```bash
.venv/bin/python -m pytest api/tests/ -q
```

Result (manifest, all 10 entries `test_result: pass`, re-run here to
confirm): **184 passed, 2 skipped, 0 failed** — observed live via the command
above (`184 passed, 2 skipped, 40 warnings in 41.07s`). Per-issue
regression tests are listed in section 2 (9 new tests, one per fixed issue;
Issue 10 deferred with no test). Tests that encoded the old vulnerable
behavior were updated to the new intended behavior (`test_guards.py`
image-bypass tests, `test_sut_genai_client_retry.py` no-resend →
must-resend test, pipeline/temporal catalog-registration fixtures).
