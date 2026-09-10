# Zero-review loop — evidence-guard precision + eval re-runs

> Last updated: 2026-09-10. Companion to `docs/review_triage.md` (numbers)
> and `docs/hallucination_fixes_2026-09-06.md` (the audit this implements).
> Covers `api/scripts/audit_review_causes.py` and
> `api/scripts/merge_eval_runs.py`.

## Problem

The 2026-09-09 eval (`qwen2.5:3b`, 14 pages) had a **100% `needs_review`
rate (294 flags)**. Triage showed the dominant causes were validator false
positives, not LLM hallucinations: the model wraps spans in `"…"`, `'…'`,
`[…]` (139 flags), and the strict substring check broke on OCR spacing
noise (`201 6-07-15`), ISO-normalized dates, and JSON-escaped newlines —
while every one of those values was correct (the judge confirmed values
like `po_number='10256'`). A second block (10 flags) came from catalog
`required` flags for fields no PO/DN in the gold set prints
(`total_amount`, `currency`, `supplier_name`, `delivery_date`) — requiring
them forces invention or permanent review.

## What changed (all strict-direction: flag, never drop)

- `api/app/core/security.py` — `strip_wrapping_quotes` (`"'…'"`, backtick,
  `[…]`, `(…)` + `\n`/`\t` unescape); `collapse_ocr_spacing` (digit-split
  tolerance); short spans (≤3 tokens) must be contiguous, longer spans keep
  the 0.75 overlap fallback; `value_in_text` is date-aware (ISO vs printed),
  comma-decimal aware (`153,50` == 153.5), dot-drop aware (`87 45` == 87.45,
  dot-reinsertion only — `1 200` still ≠ 12.00); empty document text always
  flags; the `is_image_extraction` blind-trust bypass is removed.
- `api/app/agents/validator.py` — table cells fall back to document-grounded
  value check when a row-level span is imprecise (phantom values absent from
  the text still flag); empty-text array rows flag instead of passing.
- `api/app/schemas/llm_schemas.py` — `ExtractedFieldEntry.source_span` is
  required non-empty, so span-less output retries instead of limping to a
  review flag.
- `api/app/services/extraction_service.py` — judge skip additionally requires
  `_all_numeric_spans_verbatim`; `info`-severity judge confirmations no
  longer force review (only `warning`/`error` or score < 0.7 do).
- `api/app/agents/judge.py` — prompt carries per-field `value + source_span`
  provenance so the judge checks grounding, not just values.
- `api/app/services/field_catalog.py` — effective new-field bar
  `max(configured, 0.8)`; Thai `ไม่มีข้อมูล`/`ไม่ระบุ` are placeholders
  (omitted, never extracted/registered).
- `api/app/agents/extractors.py` — prompt: spans are bare verbatim quotes
  (no quotes/brackets/labels/paraphrase); no-data placeholders banned.
- Catalogs — PO `total_amount`/`currency`/`supplier_name` and DN
  `delivery_date` are `required: false` (0/4 and 1/2 gold coverage;
  requiring unprinted fields forces hallucination).

## Verification (prevention gates)

- `api/tests/test_evidence_precision.py` (new, 20 tests) + updated bypass
  tests + `api/tests/test_catalog_required_alignment.py` (prompt rules +
  no-required-without-gold-coverage). Full suite: **337 passed, 2 skipped**;
  `ruff check api/` clean.
- Replay gate: new guard code over the recorded 09-09 predictions drops
  evidence flags **233 → 18 (92%)**; the 18 left are genuine errors.
- Live loop on `ollama-local qwen2.5:3b` (3h cap forced two chunks, merged
  with `merge_eval_runs.py`): **review rate 100% → 64% (5/14 fully clean,
  4 judge-skipped), macro F1 0.439 → 0.446, router accuracy still 1.000**,
  triage flags 294 → 60, quoted-span 139 → 0, missing-required 10 → 0.
- Follow-up local loop (fail-fast gate + bracket-strip + cell fallbacks):
  **flags 60 → 50, macro F1 0.445, review still 64%**, ICR 2088s → 73s.
- Array-key loop (`_row_claimed_values`; dict keys never verified as
  values): **flags 50 → 33, genuine-mismatch 18 → 4, review 64% → 57%
  (6/14 fully clean, 5 judge-skipped), macro F1 0.445, router 1.000**.
  Invoice1 went fully clean live. Remainder = genuine small-model errors
  for the stronger-model loop (blocked: no cloud key on this host; 7GB
  RAM rules out local 20B).

## Noted exceptions (stay flagged by design)

- `ICR.png` — **fail-fast OCR-coherence gate** (`is_ocr_text_coherent`,
  threshold 0.35 tuned on the gold set: ICR 0.29, clean pages ≥ 0.42):
  extractor timeout ×2 (~2000s stalls on trilingual soup) is now an honest
  `failed_stage: ocr` error in **~63s**, extractor never called. Needs a
  stronger model or better OCR, not looser guards.
- `Invoice2.jpg` — OCR-mangled date (`25/10/2 ด 17`) copied verbatim but
  unparseable; invented `THB` currency.
- `THAI_RECEIPT.jpg`, `Thai(invoice)+EN(Purchase).pdf p1` — heavy Thai OCR
  noise + model misreads; `Invoice+purchase.pdf p1/p2`, `Invoice1.jpg`,
  `THAI_bill.jpg`, `Delivery_note2.png` — invented currencies/totals,
  phantom line-item quantities, duplicate table columns: genuine small-model
  errors the guard correctly keeps flagging.

## Next loop (approved: model upgrade)

Re-run `--all` on a stronger model (e.g. `gpt-oss:20b` via ollama-cloud);
the remaining flags are all value-level model errors that validator tuning
must not touch. Track review rate + macro F1 jointly — a review-rate drop
from guard-loosening alone is rejected. `merge_eval_runs.py` stays for
chunked runs over the 3h cap.
