---
# Extractor prompt hardening + live smoke on X51009568881.jpg (2026-09-18)

## Prior prompt wording (api/app/agents/extractors.py _COMMON_RULES, before this change)
(a) Required-but-absent — an anti-fabrication line already existed but proved insufficient:
"- Omit absent/unreadable fields, including REQUIRED catalog fields too; never invent currency, totals, or IDs. ... Never emit a 'no data' placeholder in any language (including ไม่มีข้อมูล) as a value — omit the field."
Notably the identifier rule's own example (`invoice_number is the number after INV No./Invoice No. (e.g. '1126679')`) matches the exact fabricated value seen live — likely priming the invention.
(b) Completeness re-check against every catalog field — absent. The prompt lists catalog fields ("use these names verbatim") but never instructs a final pass over all of them. Few-shot is OFF by default, so additions stayed at sentence scale.

## Diff (additive only; no bullet reworded/removed; router/judge/schemas/acceptance/validator/timeouts/few-shot untouched)
1. New bullet after the omit-the-field rule: "'Required' means the catalog expects the field on well-formed documents, never permission to invent it: if no matching text is printed, return the field as null or omit it — never a plausible-looking guess (do NOT emit 'INV No: 1126679' when no such label is printed)."
2. New bullet before the Tables block: "Before finishing, check your answer against EVERY field in the catalog list above, not just the fields spotted on first pass: a company name in the letterhead or a total on a totals line must not be skipped."
3. result_cache.py PROMPT_VERSION "prompts-v4-canonical-judge" → "prompts-v5-extractor-completeness" (fingerprint hashes the version string, not prompt content — the bump is what forces cache miss).
4. One-line test maintenance: test_handwriting_ocr_recovery.py:157 pin "prompts-v4" → "prompts-v5" (change-detector tracking the approved bump; identical assertion strength).

## Test results
- ruff on touched files: clean.
- Prompt-asserting suites (table_column_alignment 9, field_name_evidence_guard 15, review_contracts 14, catalog_required_alignment 5, acceptance 18, field_catalog 6, handwriting_ocr_recovery 7): all pass.
- Full suite: unchanged vs pre-existing baseline (9 collection errors + 28 region/budget/streaming failures, reproduced with these changes stashed).

## Live smoke outcome (ONE dispatch, job 5d874389-c97c-4f26-abce-1b55516fa15b, ~24s, cache miss proven by result_cache_misses 1.0 / real LLM timings)
- Proposed: invoice_number "1126679" (still fabricated — anti-fabrication sentence did NOT stop the proposal this run) → rejected downstream as hallucination, 0 accepted. line_items all-"..." row → structurally rejected with the new "placeholder column key" message.
- seller_name / invoice_date / total_amount: NOT proposed at all (accepted or rejected) — completeness sentence did NOT make the model attempt them this run.
- needs_review True, completeness_score 0.0, 0 accepted fields — identical to the 0.0/0-fields baseline. No regression, no recall gain on this doc.
- No API restart was needed (uvicorn --reload child postdated the edits).

## Explicit note (one sample only)
A single run in either direction does NOT prove general behavior. The bottleneck on this receipt remains extractor recall, not acceptance. RECOMMENDATION (follow-up, not now): repeat this same smoke across a handful of other documents before considering the prompt change validated or closed. Do NOT iteratively tune wording off single samples.
---
