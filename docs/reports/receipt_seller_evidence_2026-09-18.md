# Receipt seller evidence (acceptance v1.0.1) — 2026-09-18

## Problem

POS/till receipts (e.g. SROIE `sroie_X51006857265.jpg`, MPH Bookstores) wiped
to **0 accepted fields**: the supplier role gate
(`acceptance.py::_role_evidence_present`, 240-char window over
`SUPPLIER_ROLE_HINTS`) never passes because narrow receipts print no
`supplier/vendor/seller` role word. `seller_name` (required) was rejected as
`semantic: ... lacks surrounding role evidence`, cascading to `Missing
required field` + `needs_review` + Judge `flagged`.

The `invoice_number` rejection in the same job
(`value not supported by source_span`) is a separate span-pairing issue: the
LLM paired a label-only quote with a value from another line. That rejection
is correct and stays rejected — the prompt now says so explicitly.

## Fix (safe direction only — no verification loosened)

- `api/app/services/acceptance.py::_receipt_seller_evidence_present`: the
  merchant header passes as the seller **iff** (a) the value occurs on a
  header line (first 800 chars) with no till-staff keyword
  (`cashier/staff/served by/attendant/operator`), and (b) the page carries
  ≥2 fiscal markers (`cashier/gst/vat/receipt/invoice no/tax invoice/total/
  change/tendered/till/served by/tel`). Otherwise falls through to the
  strict role reject. Cashier/staff lines, mid-page names, and non-receipt
  pages still reject.
- `api/app/agents/extractors.py::_COMMON_RULES`: quote the value's own
  printed line including its digits; never pair a label-only quote with a
  value from another line; on POS receipts the merchant header is the seller.
- `ACCEPTANCE_POLICY_VERSION` bumped `v1.0.0` → `v1.0.1`
  (`api/app/schemas/documents.py`); hardcoded `"v1.0.0"` stamps in
  `extraction_service.py` now use the constant so cache fingerprinting and
  result records agree. Old cached entries invalidate automatically.

## Deliberately NOT changed

Per `docs/reports/hallucination_fixes_2026-09-06.md` issues 4–6: no
"appears elsewhere" fallback, no short-span overlap fallback, no empty-text
pass, no leading-zero-stripped acceptance (`8558` for printed `008558`
stays rejected — the stored ID must keep its zeros).

## Verification

```bash
source .venv/bin/activate
ruff check api/
python -m pytest api/tests/test_acceptance.py api/tests/test_security.py \
  api/tests/test_review_contracts.py api/tests/test_evidence_precision.py \
  api/tests/test_guards.py api/tests/test_catalog_required_alignment.py \
  api/tests/test_pipeline.py -q
```

New regression tests (`api/tests/test_acceptance.py`, synthetic MPH slip):
`test_receipt_seller_header_accepted_via_fiscal_markers`,
`test_receipt_fallback_rejects_midpage_name`,
`test_receipt_fallback_needs_fiscal_markers`,
`test_label_only_span_still_rejected`.

## Operator note

Re-upload the receipt after deploy (policy bump invalidates the stale cached
result). If fields still reject, expand `▶ OCR text` and compare
`rejected_candidates[].raw_evidence` against the page text: a span absent
from the OCR text means Tesseract mangled it (try `OCR_LANGUAGES=eng` for
Latin-only slips), not a grounding bug.
