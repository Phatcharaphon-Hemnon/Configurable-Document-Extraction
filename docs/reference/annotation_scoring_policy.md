# Annotation & scoring policy — existing behavior vs proposed (proposed v1.0.0)

> Scope: active gold set `api/app/data/knowledge_base/ground_truth/manifest.json`
> (v2, 8 SROIE pages). Existing evaluator: `api/scripts/run_eval.py`
> (`score_page`, `match_value`, `summarize`) + `app/services/field_matching.py`
> (`values_match`) + `app/schemas/evaluation.py`. This document separates what the
> evaluator DOES today from what is PROPOSED. Nothing here changes scores until a
> new gold version is approved by human review.

## 1. Existing scoring behavior (verified against code)

- **Field names:** `normalize_field_name` (trim → lowercase → whitespace/hyphens→`_`,
  collapse repeats; `field_catalog` additionally strips leading/trailing `_`).
  Exact match only; aliases/synonyms never map (`invoice_id` ≠ `invoice_number` —
  covered by `test_ids_keep_leading_zero_and_no_alias_matching`).
- **Digit-string IDs:** `match_value` short-circuits: both-strings digit-only →
  exact string equality (leading zeros preserved: `00123` ≠ `123`, `123.0` ≠ `00123`).
- **Numbers:** `_try_parse_number` strips `$€฿`, removes ALL commas, `(x)`→`-x`,
  epsilon `1e-6`. Consequence: `153,50` parses as `15350` (NOT 153.5); `87 45`
  (space) does NOT parse as numeric → falls to string compare. Ambiguous
  thousand/decimal separators are therefore NOT reliably handled — recorded as a
  known limitation, not a rule to rely on.
- **Dates:** `_try_parse_date` tries `KNOWN_DATE_FORMATS` only
  (`%Y-%m-%d`, `%d/%m/%Y`, `%m/%d/%Y`, `%d/%m/%y`, `%m/%d/%y`, `%d-%m-%Y`,
  `%m-%d-%Y`, `%d-%m-%y`, `%m-%d-%y`, `%B %d, %Y`, `%b %d, %Y`, `%d.%m.%Y`,
  `%d.%m.%y`). Pure-digit strings never parse as dates. **No Buddhist Era
  conversion exists**: `29/12/2558` parses via `%d/%m/%Y` as year 2558; a Gregorian
  `29/12/2015` would NOT match it. BE dates must stay BE verbatim; equivalence to
  Gregorian years is semantic interpretation, not literal scoring.
- **Strings:** whitespace-collapsed, case-insensitive equality. `EMMERTON-LAMBERT`
  vs `EMMERTON - LAMBERT` do NOT match (hyphen spacing is significant).
- **Nulls:** `values_match(None, None)` is True; `score_page` never calls it with
  `None` expected except via `predicted.get(k)` → missing prediction vs `None`
  expected would count as match — but gold `fields` never contain `None` today;
  **blank table cells are `null` in `rows` and are excluded from scoring**
  (`excluded_cells` counter, `cells_total` not incremented). This is current
  policy, not a universal rule.
- **Tables:** ordered rows; predicted table chosen by max printed-header overlap
  (normalized labels); per-cell `match_value`; null ref cells skipped; extra
  rows/columns counted separately (`extra_rows`, `extra_columns`); missing pages
  (`doc=None`) score precision=recall=0 and stay in denominators.
- **Excluded fields:** `gold.excluded_fields` removes keys from BOTH expected and
  predicted before scoring (so predicting an excluded value is not penalized
  today — a known leniency, kept for backward compatibility).

## 2. Proposed annotation states (pending approval — NOT yet scoring)

Six disjoint states; `null` must never mean all of them:

| State | Meaning | Scoring (proposed) |
|---|---|---|
| `present/readable` | Printed and legible; transcribed verbatim | Scored |
| `absent` | No such printed field on the page | Not in gold; prediction = FP |
| `blank_cell` | Printed cell exists but is empty | `null` ref, unscored (matches current evaluator) |
| `ambiguous/illegible` | Printed but unreadable, or reading needs guessing (handwriting, Thai-script ambiguity, crossed-out lines, pre-decimal notation) | Listed in `excluded_fields` + notes, unscored |
| `unannotated` | Printed and readable but missing from gold (annotator miss) | **NOT auto-scored**: record as candidate, score only after approval in a new gold version |
| `excluded` | Explicit policy exclusion with reason (e.g. DD timestamps, salesperson IDs, Wi-Fi passwords, advertising, signature scrawls) | Unscored with reason |

## 3. Proposed normalization clarifications (pending)

- Keep literal-transcription scoring as the default; report any semantic
  equivalence (BE↔Gregorian, `0`↔`0.00`, `£89.00`↔`89`) as a separate,
  explicitly labeled analysis — never silently merged into the headline F1.
- Numeric separators: do NOT assume `,` = thousands or decimals; Thai/European
  variants require per-page evidence. Current code strips commas — proposed
  policy flags this as a limitation to fix in a versioned scorer change, not by
  hand-editing gold values.
- Repeated columns keep positional identity (`s_price`, `s_price__2`) and order;
  never merge. Column matching stays on exact normalized printed headers.
- Identifiers, dates, and amounts keep full literal precision in gold
  (leading zeros, BE year, two-decimal amounts); any folding happens in the
  scorer and is versioned.

## 4. Coverage accounting (proposed)

Report separately: scored fields/cells, blank unscored, ambiguous unscored,
excluded with reasons, and (after approval) newly added values. Split
readable-reference coverage from ambiguous-reference coverage so review-driven
exclusions cannot inflate scores silently.
