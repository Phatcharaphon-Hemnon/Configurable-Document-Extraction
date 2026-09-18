# Evidence + script-selection hardening (receipt hallucination guard)

Three coordinated fixes for the receipt failure mode where the extractor
quotes a field/catalog name as `source_span` (e.g. `order_id` instead of
`"593101"`), cells resolve off-page, and English print is mis-tagged `th`.

## 1. Extractor prompt (`api/app/agents/extractors.py`)

`_COMMON_RULES` now states the evidence rule with a receipt example:

> `source_span` must quote the VALUE as printed, never the field catalog
> name, column header, or printed label: quote `'593101'`, NOT `'INV No:
> 593101'`, NOT `'Order ID'`, NOT `'order_id'`.

The guard stays strict (verbatim check untouched); only the instruction got
explicit.

## 2. Field-name span guard (`check_evidence`, `api/app/core/security.py`)

- New `is_field_name_span(span, known_names)`: true when the normalized span
  (plus its underscore-insensitive variant) exactly matches a known catalog
  name/label — e.g. `"order_id"` or `"Order ID"`.
- `check_evidence(..., known_names=None)` rejects such spans up front with
  `"source_span quotes a field name/label, not document content (possible
  hallucination)"`, even if the label words appear somewhere in the
  document. `known_names=None` (default) preserves old behavior for legacy
  callers.
- Wired through `accept_page` (`api/app/services/acceptance.py`, fields +
  cells, via `catalog_label_set`) and the validator's legacy cell re-check
  (`api/app/agents/validator.py`).

Acceptance-policy version bumped `v1.0.0` → `v1.1.0`, and the two places in
`extraction_service.py` that stamped the version literally now import
`ACCEPTANCE_POLICY_VERSION`, so the result-cache fingerprint tracks the
stricter policy.

## 3. Hybrid OCR script selection (`api/app/services/hybrid_ocr.py`)

New `select_script_reading(th_text, conf_th, en_text, en_conf)` centralizes
the per-region TH-vs-EN decision, replacing the ad-hoc branch chain:

- Empty TH, or TH with **no Thai script and no ambiguous-script letters**
  (Latin-only, digit-only like `"593101"`, symbol-only) + non-empty EN →
  **EN wins**. Verbatim agreement keeps the TH confidence and only flips
  the engine tag to `rapidocr-en`.
- Any genuine difference (both readings non-empty and unequal, including
  Latin-only disagreements) → EN wins provisionally with `conflict=True`;
  the caller retains the TH alternative + review reason (conflicting
  readings are never silently dropped).
- EN empty, or verbatim Thai-script agreement → TH (unchanged).

This stops Latin-only blocks from being selected as Thai-model readings,
which previously corrupted `page_text` with Thai fragments and pushed the
router toward a wrong `th` language tag.

## 3b. Latin-page Thai-override post-pass (`apply_latin_page_override`)

Defense-in-depth behind the selector above: when the page's EN readings
are Latin-majority (`page_latin_fraction` > 0.70, letters-only vote —
digits/symbols are script-neutral), any remaining Thai-only selection
(`is_thai_only_block`: Thai script, no Latin, no other scripts) with a
non-empty Latin EN alternative is overridden to English, keeping the TH
reading as an alternative + `latin-page-thai-override` review reason.
Regions without an EN reading (confident-Thai skip, crop/EN failure) are
left untouched — the pass re-selects only, never re-recognizes.

OCR output changed, so the hybrid cache fingerprint bumped
(`HYBRID_PREPROCESS_VERSION` default `hybrid-v1` → `hybrid-v2` in
`api/app/core/config.py`; env override still wins).

The router prompt additionally bases the language tag on the MAJORITY
script of printed words, explicitly excluding OCR artifacts, borders,
ruled lines, and separators — stray Thai-looking noise never makes an
English document `th`.

## Tests

- `api/tests/test_field_name_evidence_guard.py`: guard fires on
  `source_span == "order_id"` (incl. `"Order ID"` variant), stays silent
  without `known_names`, prompt contains the receipt rule, `accept_page`
  rejects a field-name span end-to-end.
- `select_script_reading` cases live in the same file (latin-only →
  English, digit-only → English, Thai-script agreement → TH, conflict flag).
