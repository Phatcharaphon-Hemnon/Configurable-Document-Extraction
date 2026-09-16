# Screenshot-driven findings (fixtures re-run where available)

Attachments `2459fb76…`, `877868a4…`, `137ca538…`, `37008434…`, `deb2fb44…`
were unavailable here (`/workspace/scratch/…` absent, verified); analysis
below uses the task's screenshot descriptions plus the preserved runtime
originals (`data/regression_refs/`, hashes in `annotations.json`) and saved
outputs (`data/backups/20260912T053413-pre-implementation/`). Observed
symptoms vs confirmed causes are separated; nothing is marked fixed on unit
tests alone.

| # | Fixture / screenshot | Observed symptom | Confirmed cause (code path) | Reproduction evidence | Fix implemented | Regression test | Remaining limitation |
|---|---|---|---|---|---|---|---|
| C1 | `invoice_form.webp` blank form | `seller_name="Client name:"`, `total_amount="ยอดรวม"`, address/tax labels as values; coverage 75% | Model returned captions as values; validator flagged spans as found (captions ARE in text) but never rejected label-as-value (`validator.py` old, `acceptance.py` absent) | Saved output `saved-output-invoice_form.webp-….json` shows invalid fields accepted with 100% confidence | `acceptance.py` label-as-value rejection (catalog-derived labels), coverage from accepted only | `test_blank_labels_rejected_not_accepted` | Role/type edge cases with novel captions need catalog Thai-label coverage |
| C2 | blank form currency | THB shown; was it rejected? | Old code had no currency-evidence rule (screenshot Judge flags span mismatch instead) | Same saved output | Explicit currency-evidence rule; printed THB accepted iff mark present (`test_printed_thb_accepted_when_explicit`) | acceptance tests | Symbol variants beyond known set need extension |
| D1 | `3492511_1.jpg` handwritten | `invoice_date="253 KINGS ROAD"` (address as date) | Type gate missing: `validator` flagged unparseable but field stayed accepted; coverage counted it | OCR bench: Tesseract salad (coherence 0.409, blocks `KAYBERY`, `253/KINGS/ROAD`, Thai fragments); saved output shows date accepted | Date-type rejection as unresolved, never guessed | `test_invalid_date_and_amount_rejected` | Handwriting still assistance-grade; EN/TrOCR recovery needs models installed (absent here) |
| D2 | handwritten currency | `currency="THB"` without evidence | No currency-evidence check (old) | Saved output + OCR text has no THB mark | Unsupported-currency rejection | `test_unsupported_currency_rejected` | — |
| D3 | handwritten language | English source labeled `th` | Router language hint from Thai-script salad; independent of acceptance | OCR blocks show Thai misreads of Latin hand | Kept as observed router behavior; role/type fixes don't rely on language | — (router unchanged per constraints) | Language label on salad remains noisy |
| D4 | handwritten table | `ไม่มีข้อมูล` in every cell, 1 row × 4 cols | Model emitted placeholders; nothing filtered them (`extractors` drops field placeholders, not table cells) | Saved output table | Placeholder cell/row rejection, row withheld unless fully grounded | `test_wrong_row_and_invalid_structures_rejected` | Cell-level spans required; row-span quotes now correctly rejected (see `test_row_span_cell_requires_cell_level_evidence`) |
| D5 | handwritten confidence | Invalid fields at 100% | Model returned 1.0; pipeline preserved it (correct) but UI showed no rejection state | Saved output confidences 1.0 | Confidence kept as model estimate; rejected status shown separately; never defaulted to 1.0 (`extractors` clamps, defaults 0.0) | acceptance + UI labels | A model that returns 1.0 still displays 1.0 (as estimate) with rejection flag |
| E1 | `Invoice+purchase.pdf` p1 | Duplicate `S/PRICE` columns | Unconfirmed: could be mutable `last_tables` leak OR model hallucination. `last_tables` removed (typed `extract_call`), but no live 3-page rerun with models here proves the first layer | Saved output shows dup keys flagged (`duplicate column keys`) | Typed page-bound calls; positional keys for repeats (`s_price__2…`); strict row/key validation | `test_three_pages_isolated_no_leakage`, `test_duplicate_columns_get_positional_keys` | First-layer attribution unverified live; needs a warm-model 3-page rerun |
| E2 | PO page extra `S/PRICE` 68.90/17.49 | Values not visible in preview | Same as E1: leak vs hallucination unresolved without layer trace | Saved output PO table | Same fixes + wrong-row/total guards | page-isolation tests | Unresolved until instrumented rerun |
| E3 | Column-count mismatch (4 declared vs wider render) | Count derived from different structure than rendered | Frontend derived counts from `table.columns.length` (same structure) but legacy scalar fallback could diverge | Code inspection | Counts now explicitly from rendered structure; fallback labeled legacy | UI fallback filter | Legacy records still show fallback where no validated table exists |
| E4 | Language labels vary across pages | `th`/`en` mix | Per-page router calls (correct); salad-driven hints | Saved outputs p1 th, p2 en, p3 th | No change (per-page binding is correct) | page-isolation asserts per-page language passthrough | — |
| F1 | PO buyer=supplier `Paula Parente` | Same name both roles; source shows Customer Name only | No role-evidence check (old): name found somewhere sufficed | Saved output + OCR text | Role-window check: buyer kept (Customer evidence), supplier rejected | `test_buyer_supplier_roles_require_evidence` | Genuine dual-role orgs need both role marks on page |
| F2 | Judge "should be X but is X" + absent-claims despite evidence | Contradictory/mechanical messages | Judge prompt lacked structured targets; no reconciliation; normalization blind to OCR spacing | Saved outputs judge issues | Structured issues + `reconcile_judge_issues` (discard only objectively false mechanical claims; keep semantic/row even on string match) + dedup | `test_judge_reconciliation_keeps_semantic_discards_false_mechanical` | Judge quality still model-bound |
| G1 | 100% confidences + green stepper on flagged results | Checkmarks imply correctness | Stepper tracked stage execution (=4 when judge ran), not data verdict; confidence rendered bare | Code inspection | Stepper labeled "executed, not correct"; Data badge (accepted/needs-review/unresolved/legacy); confidence labeled model estimate; skipped/unavailable never "passed" | UI (build passes) | Visual audit needs browser run |
| A | Missing PNG attachments | `No such file` on 5 UUIDs | Session-specific `/workspace/scratch/…` path; absent here (verified) | `annotations.json` missing_attachments | Request missing attachments; continued on fixtures | — | Visual pixel-check of those 5 not done |

## What was re-run vs not

- Re-ran: OCR (cold, caches bypassed) on all three fixtures; full backend suite
  (395 passed); frontend build + queue tests; cache parity/invalidation tests
  (isolated, synthetic payloads).
- Not re-ran live: full LLM pipeline on originals (qwen2.5:3b ≈ 8–10 min/page
  measured historically; no 60s-compliant path on this CPU — reported as the
  remaining gap, not claimed).
