# Acceptance v1.1.0 — invoice letterhead supplier + placeholder-cell relaxation (2026-09-18)

## Symptom (X51008099090.jpg, Restoran Wan Sheng receipt)

Pipeline completed technically but every page landed in `needs_review` with
`completeness=0.75` and the line-items table dropped entirely:

- `semantic: supplier role for 'RESTORAN WAN SHENG' lacks surrounding role
  evidence` → `seller_name`/`seller_address` rejected → `Missing required
  field: seller_name`
- `line_items/0/tax: placeholder cell omitted` → `row withheld` (×2 rows) →
  `table withheld: no fully grounded rows`
- Judge then failed truncated (`finish_reason=length`, max_tokens=1000),
  compounding with `Judge unavailable`.

## Root causes

1. **Role-evidence rule had no receipt answer.** Receipts print the selling
   organization as the page letterhead with no `Seller:` label — the strict
   "role keyword near the value" rule can never pass there, by design of the
   document layout.
2. **Placeholder conflated with unresolved.** A placeholder cell ("N/A",
   "ไม่มีข้อมูล") means *absent data* (info-level), yet it set `row_ok=False`
   and withheld the whole row/table — identical treatment to a cell with
   unresolved evidence.
3. **Judge output ceiling too small** for the structured-issues schema
   (target/evidence/explanation per issue): 1000 tokens truncated
   (`finish_reason=length`) leaving the page without a judge verdict.

## Changes (policy v1.1.0 — `ACCEPTANCE_POLICY_VERSION` bumped, cache-fingerprinted)

- `app/services/acceptance.py`
  - `_supplier_header_evidence()`: on **invoices only**, a supplier/seller
    field whose value sits in the letterhead region (first 400 normalized
    chars, OCR reading order) is accepted with an **info** issue
    ("supplier accepted by invoice letterhead position") — never silently.
    Guards: value ending in a colon or containing buyer-role vocabulary
    ("Client name:") is a caption and never laundered into a supplier.
    **Purchase orders are excluded** — a PO's letterhead is the BUYER.
  - Placeholder cells no longer set `row_ok=False`: rows keep their grounded
    cells; only unresolved cells (no refs / span mismatch) withhold a row.
  - Columns with no accepted cell in any kept row are dropped from the
    accepted table (info issue) — e.g. an all-placeholder `tax` column.
- `app/agents/validator.py` — structural gate on accepted tables relaxed to
  duplicates/unknown-keys/empty-row errors. Safe because acceptance
  guarantees an accepted row was structurally complete on input: a missing
  column in a kept row can only be a placeholder-omitted cell.
- `app/agents/judge.py` — `max_tokens` 1000 → 2000 (structured issues fit;
  bounded, still token-minimal).
- `app/schemas/documents.py` — `ACCEPTANCE_POLICY_VERSION = "v1.1.0"` (old
  cached results invalidate via fingerprint).

## Verification

- `api/tests/test_acceptance.py`: 5 new tests (letterhead accept, mid-page
  no-label still rejected, PO not relaxed, all-placeholder column drop,
  mixed placeholder/zero row + full `validate_detailed` clean page). 14/14
  pass; pre-existing test `test_buyer_supplier_roles_require_evidence`
  (PO role evidence) unchanged and green.
- Full suite: failures byte-identical to the pre-change baseline (35
  pre-existing branch-state failures, none new).
- Live e2e (real `X51008099090.jpg`, `ollama-cloud/gpt-oss:20b`):
  `doc_type=invoice completeness=1.0 needs_review=False`,
  `validation_errors=[]`, 10 fields accepted (seller via letterhead),
  line_items 2 rows kept (tax column dropped, all placeholder), judge
  skipped as clean, 53.5s end-to-end.

## Trade-off (accepted)

Letterhead-position acceptance weakens the role-evidence guarantee for
invoice supplier fields: an unusual invoice printing the buyer at the top
would mis-assign. Scope (invoice-only + caption guards + info visibility)
keeps that window narrow; genuine role-labeled documents are unaffected.
