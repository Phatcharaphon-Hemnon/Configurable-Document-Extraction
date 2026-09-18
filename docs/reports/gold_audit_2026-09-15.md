# Gold-set audit report — 2026-09-15 (offline, no live inference)

> Status: AI-assisted/provisional. No human verification is claimed. Every
> review entry stays `pending` until a reviewer supplies identity + timestamp.
> Historical reports, predictions, and runtime History are preserved unchanged.

## 1. Frozen evidence

- HEAD snapshot at audit time: branch `perf/latency-opt-20260914` (ahead of
  origin, dirty tree with unrelated region-extraction changes — intentionally
  NOT committed with gold artifacts).
- `manifest.json` version 1, sha256
  `589dd06f59b3eb28ac1027e18710be1cae22b62f778ad9c4dc71e57fe9eb6d4d`,
  `annotation_method`: "Assistant visual transcription … provisional, not
  independently human-adjudicated".
- Inventory: 12 files / 15 pages (3 invoices×Malaysian receipts, 2 Thai
  receipts, 1 blank bilingual form, 2 delivery notes, 4 PO pages incl. 2
  multi-doc PDFs, 2 handwriting pages). All 12 source hashes verify OK;
  PDF container page counts match manifest (`Invoice+purchase.pdf`=3,
  `Thai(invoice)+EN(Purchase).pdf`=2, all others 1); `page_number` sequences
  are `1..N`; no missing sources, hash mismatches, or duplicate
  `(filename, page)` labels.

## 2. Visual audit (source = rendered page; OCR/predictions never authoritative)

Assistant inspected every reference page render. Findings are recorded in
`review_package.json` (`ai_audit_finding`, `annotator: AI-assisted/provisional`,
`human_status: pending`) — separate from any human verdict.

- **Roles from full visual context** (not the runtime ~240-char heuristic, which
  is itself under review): `Your Company Name` (Delivery1) and `East Repair
  Inc.` (Delivery_note2) read as senders; `Buyer Ltd./Billy Buyer` and
  `John Smith` as recipients; PO `Customer Name` (Paula Parente, Karl Jablonski,
  Horst Kloss) reads as buyer with no supplier printed. The acceptance role
  rule may be wrong/incomplete — hence review questions, not corrections.
- **Placeholders vs data** (Delivery1 `000-000-0000`, `Your Company Name`;
  blank Thai form IDs/dates/companies): values match print, but whether
  template/blank labels count as scored data is a policy review question.
- **Two addresses** (Delivery_note2 DELIVER TO vs SHIP TO): manifest records
  SHIP TO — review question, no change.
- **Handwriting** (3492511_1, ICR): exclusions justified (date/seller/amounts/
  item words ambiguous; pre-decimal `£/s/d` never reinterpreted; crossed-out
  lines decorative). ICR date `12/7/2014` moderate confidence; trailing `86`
  reads as separate annotation. 3492511 `44`, `EMMERTON-LAMBERT`,
  `253 KINGS ROAD, CHELSEA, S.W.3` confirmed as the only confident fields.
- **Tables**: headers/order/row boundaries verified; repeated GST columns kept
  positional; `null` blank cells unscored; printed inconsistent totals preserved
  (Delivery_note2 9.07/154.06); `Invoice1.jpg` code+description combined under
  the single printed `Item` column (review question on granularity).
- **Thai pages**: amounts/dates/IDs match print; BE dates kept verbatim;
  Thai-script names/addresses provisional (native-speaker check pending);
  `THAI_bill.jpg` branch/time are derivations (review question);
  `THAI_RECEIPT.jpg` buyer-name exclusion justified.

## 3. Proposed corrections (pending — NOT applied)

`proposed_corrections.json` (2 items, both `human_status: pending`):

1. `Invoice2.jpg#p1:field:tax_id`: `000381399440` → `000381399040` (visual
   reads `…99040`; middle digits ambiguous at current resolution — needs
   high-res crop + human confirm; the two strings do NOT match under the
   existing scorer).
2. `3492511_1.pdf#p1:field:bill_to_name`: `EMMERTON - LAMBERT` →
   `EMMERTON-LAMBERT` (visual hyphen has no surrounding spaces; likewise
   non-matching under existing scorer — literal transcription fix only).

All other disputes are review questions with `proposed_value: null`.

## 4. Leakage audit (data flow, not path strings)

- **Generation**: router prompt = filename + sanitized page text; extractor
  prompt = compact catalog + sanitized page text + few-shot examples only
  (`extractors.py::_build_prompt`). No `ground_truth`/manifest reference in any
  generation path. Few-shot defaults OFF (`0`).
- **Retrieval**: TF-IDF ranks `few_shot/*` examples on `input_text`+`description`
  only; `output` answers excluded from retrieval text. Ground truth never
  retrieved.
- **Discovery writes**: `register_discovered_fields` gates on placeholder,
  snake_case, confidence ≥ max(configured, 0.8), plus `check_evidence` against
  the current page text. `run_eval.py` uses an isolated temp KB (catalog copy
  only), DB/audit/Temporal disabled — gold answers cannot enter the catalog.
- **Cache**: `result_cache.fingerprint_page` covers file bytes, page text, OCR
  config/models, provider models, generation settings, prompt version
  (`prompts-v4-canonical-judge`), catalog hash, and acceptance policy version.
  Gold values are neither key nor stored value.
- **Post-extraction scoring only**: `knowledge_base.get_ground_truth` (legacy
  per-stem `<stem>.json`) is read AFTER `accepted_fields` are computed, purely
  for the `auto-eval` trace score; no such legacy files exist for the current
  gold set (manifest-only), so it returns `None` in practice. The public
  `POST /api/evaluate` + `service.evaluate()` accept caller-supplied pairs as a
  pure function — scoring-only, never fed back into prompts, catalog, or cache.
- Import-graph guard added in tests is a tripwire only; the above flow analysis
  is the actual evidence.

## 5. Evaluation independence

All 15 pages remain **developer gold** (debugging/regression only). Prior tuning
use is recorded (evidence-guard loops, catalog `required` relaxations, TrOCR
rejection). Held-out evaluation is **blocked** pending instructor-provided or
new independently sourced/reviewed documents; related pages and same-document
variants must stay together in any future split. `eval_report.md`,
`eval_artifacts/`, and `docs/reports/*` unchanged.

## 6. Reproducibility

```bash
./scripts/run_all.sh            # setup (unchanged)
ruff check api/ && python -m pytest api/tests/ -q   # final gate
cd web && npm run build
.venv/bin/python api/scripts/run_eval.py --all --gold-dir api/app/data/knowledge_base/ground_truth --output-dir .  # live; NOT authorized in this offline audit
```

Offline only in this report: hash/page inventory, recorded-prediction coverage
(14/15 pages; `3492511_1.pdf` not evaluated historically — see rescoring
report), new `test_gold_audit.py`.
