# Acceptance gaps from X51009568881.jpg: placeholder column keys + totals-line evidence

Date: 2026-09-18. Policy: `ACCEPTANCE_POLICY_VERSION` v1.4.0 → **v1.5.0**
(cache fingerprint covers the version, so stale cached results invalidate
automatically.)

Source investigation: job `6e97cb52-…` (structurally invalid
`line_items` row, missing seller/invoice_date/total) and job `ef519c25-…`
(accepted `total_amount="40.40"` grounded to the `CASH 40.40` tendered line
while the real total sat on `Total Inclusive GST: 10.40`). OCR text was
byte-identical across both runs (coherence 0.49 ≥ 0.40, gate passes), so
both gaps are acceptance-layer, not OCR-layer.

## Change 1 — placeholder column keys (detection gap closed)

- `api/app/services/field_catalog.py`: `PLACEHOLDER_VALUES` gains `"..."`,
  `"…"`, `".. ."`. Rationale: the model emits bare ellipses for absent
  cells/keys (`bill_to_name`/`seller_email` values AND `line_items` row
  `cell.column` keys in the failing jobs). `"."` alone and decimals are
  untouched — only the three literal variants match.
- `api/app/services/acceptance.py` table-row loop: the structural
  `row_keys vs col_keys` rejection stays the primary path, but rows whose
  keys are placeholders now get the specific finding `"placeholder column
  key"` instead of the generic `"columns missing, duplicated or unknown"`.
  Cell VALUES keep their existing placeholder handling below; columns are
  structural keys, never values, so the structural branch is the only place
  they are judged. Accept/reject outcomes are unchanged (such rows were
  already rejected) — only the finding is more specific.

## Change 2 — totals-line evidence for payable totals (missing check added)

- Scope `TOTAL_ROLE_FIELDS = {"total_amount", "grand_total", "amount_due",
  "invoice_total", "order_total"}` — canonical `total_amount` plus the
  payable-total `alternative_names` observed in `invoice_fields.json` /
  `po_fields.json`. Excludes `subtotal/tax/paid/change/weight` (own
  semantics) and follows the existing hardcoded `ROLE_FIELDS_*` style
  (`FieldDefinition` drops `alternative_names` at runtime, so catalog-driven
  scoping is not available in `accept_page`).
- `_total_label_evidence_present(value, span, page_text, hints)` passes when
  a totals hint (`TOTAL_KEYWORDS` + the field's own printed label, e.g.
  `"total amount"`) is in the span, on the value's printed line, or when the
  value is the only decimal amount on the page (labelless short receipts).
  Same-line scoping is deliberate: amounts repeat across lines, so a wide
  window would let a CASH/CHANGE/SUBTOTAL line borrow the real Total line's
  label — exactly the `ef519c25` mixup. Applies only to amount-parsing
  values (`_parse_amount`), after type/evidence/label checks.
- Untouched as instructed: invoice_number's short-span verbatim rule, table
  column↔row structural matching, Group B/C items.

## Verification

- New tests (`api/tests/test_acceptance.py`, `test_field_catalog.py`):
  placeholder keys `"..."`/`"…"`/`".. ."` → `"placeholder column key"`
  finding (plus a control keeping the legacy message for genuinely-unknown
  keys); `total_amount="40.40"` on a `CASH 40.40` line → rejected naming the
  line; bare `"10.40"` span on the Total line → accepted; lone labelless
  `"5.00"` → accepted (safeguard); `grand_total` on a cash line → rejected.
- `.venv/bin/ruff check` on touched files: clean. Relevant suites
  (acceptance, catalog, security, evidence, pipeline, gold, guards, tables,
  cache, migration): **243 passed**.
- Full-suite note: 9 collection errors + 28 failures in
  region-pipeline/HTTP-budget/streaming tests are pre-existing working-tree
  breakage (missing names in `app.services.client` / `request_control` /
  `app.agents.extractors`) — reproduced with these changes stashed.

## Precision-neutrality confirmation

No evidence check was loosened: every previously-accepted fixture still
passes (incl. `Total: 0.00` zeros, letterhead seller, gold tests). The diff
only (a) rewords an already-rejecting path with a more specific finding,
(b) adds `"..."`-family to the placeholder set (never real document data,
same precedent as `"--"`), and (c) adds a rejection path for totals
grounded to unrelated lines, with an explicit lone-amount exemption so
labelless short receipts cannot newly fail.
