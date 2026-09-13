# Evidence references & acceptance policy (v1.0.0)

## Modules

- `api/app/services/evidence.py` — page-local resolution of quoted `source_span`
  text against the current page's OCR blocks/text. Assigns stable per-page
  block IDs (`page-{n}-block-{i}`), preserves original OCR text/page/bbox/reading
  order, tries raw + NFC + spacing-collapsed variants, supports multiple refs
  for multiline values. Geometry always comes from stored blocks; model
  coordinates/IDs/offsets are never trusted. Label vs value vs context roles
  stay distinguishable. Compatible with existing `source_span` consumers
  (resolution is additive).
- `api/app/services/acceptance.py` — deterministic accept/reject over fields
  and tables (`ACCEPTANCE_POLICY_VERSION = "v1.0.0"`, mirrored in
  `schemas/documents.py`; bump on any rule change — it enters the result-cache
  fingerprint).
- `api/app/agents/validator.py` — runs acceptance first, then legacy gates on
  ACCEPTED data only. `validate()` keeps the `(errors, completeness,
  needs_review)` shape; `validate_detailed()` also returns accepted fields /
  tables, rejected candidates, structured issues, coverage.

## Accepted vs rejected

Accepted data (`fields`/`tables`, normal exports) contains only supported
candidates. Rejected candidates live in `rejected_candidates` (id, kind,
location, proposed value, raw evidence, source refs, reason, findings) for
review/debug export only.

- Labels are never values (`Client name:`, `ที่อยู่`, `ยอดรวม` with no
  populated value → rejected). Matching uses catalog-derived labels
  (name/label_th/description_th normalized), never a standalone blacklist.
- Types enforced: date must parse (else `Unparseable date for …`,
  unresolved never guessed); amount must be numeric (labels/addresses
  rejected); identifiers stay strings (leading zeros preserved).
- Currency needs explicit code/symbol evidence on the page/span; never
  inferred from language or locale. Printed THB on an otherwise blank form is
  accepted when the mark is present.
- Buyer/supplier need role keywords within ~240 chars of the value
  (e.g. customer/bill-to vs supplier/vendor + Thai equivalents). Same org in
  both roles is kept only when each has its own role evidence.
- Zeros (`0`, `0.00`), leading-zero IDs (`000123`, `000-000-0000`), and
  legitimate repeated values are preserved — never auto-rejected as
  placeholders/duplicates.
- Ambiguous dates/amounts/handwriting stay unresolved (rejected + review
  issue), never guessed.
- Coverage counts accepted populated required fields only.

## Tables

- Repeated labels keep order + labels; keys become positional
  (`s_price`, `s_price__2`, …) — never merged.
- Each cell needs its own supporting span. The old fallback ("value appears
  elsewhere on the page") is removed; unbound cells stay unresolved.
- Invalid structures (dup keys after normalization, missing/dup/unknown row
  keys) rejected with reasons. Placeholder-only rows (`ไม่มีข้อมูล`) dropped,
  never fabricated. Totals detected in item rows rejected as wrong-row.
  Scalar `line_items` JSON alongside structured tables is dropped as a
  duplicate representation (table wins).
- Arithmetic only with sufficient operands/semantics (quantity × unit_price vs
  total_price, discount-aware, 0.02 tolerance); missing amounts never invented.

## Judge interplay

Deterministic validation runs before the Judge. The Judge receives structured
identifiers, values + spans, role context, validation findings, and OCR
uncertainty, and must return category/target/severity/evidence/explanation per
issue. Reconciliation discards only objectively false mechanical claims;
semantic/row/type/OCR concerns survive string matches. Skip requires all of:
no errors, no unresolved findings, coverage 1.0, data present, confidence ≥
threshold, verbatim numeric spans, no OCR uncertainty. Skipped/unavailable
never renders as passed.

## Rollback

Revert `acceptance.py`/`evidence.py`/`validator.py`/`judge.py`/`extractors.py`
and set `ACCEPTANCE_POLICY_VERSION` back; cached entries from the new policy
invalidate automatically by version. Legacy records load as `unevaluated`.
