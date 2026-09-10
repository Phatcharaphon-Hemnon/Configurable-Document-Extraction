# Review-cause triage — combined re-run (new guard code, qwen2.5:3b, 14 pages)

Source: `eval_final/eval_artifacts/predictions.json`. Total validation/judge flags: **50** across 14 pages, review rate 9/14 (64.3%).

Baseline (2026-09-09 run, old guard): 294 flags, 14/14 in review. Quoted-span false positives 139 → 0; missing-required 10 → 0. What remains is genuine model error / OCR-hard content for the stronger-model loop.

## Buckets (all pages)

| Bucket | Count | Meaning | Fix phase |
|---|---|---|---|
| quoted-span | 0 | LLM wraps span in literal quotes; strict substring fails on correct values | Phase 1 (strip wrapping quotes) |
| ocr-noise | 7 | tokens overlap ≥0.75 but substring fails (spacing/OCR errors) | Phase 1 (OCR-tolerant match) |
| date-format | 3 | ISO-normalized value vs printed date; unparseable-date strings | Phase 1 (date-aware compare) + Phase 2 (verbatim span rule) |
| missing-required | 0 | catalog requires genuinely-absent fields | Phase 3 (catalog/gold) |
| genuine-mismatch | 18 | value tokens absent from doc — real model errors | Phase 2 (prompt/model) |
| judge | 1 | judge unavailable errors | Phase 1 (judge schema) |
| judge-score<0.7 | 6 | judge ran and scored below pass | Phase 2 (model) + Phase 4 (re-run) |
| table-shape | 2 | duplicate/missing columns, arithmetic | Phase 1/3 |
| low-confidence | 0 | confidence < 0.6 | Phase 2 (model) |
| other | 13 | unclassified | — |

## Per-file summary

| File / page | Type | Errors | Buckets |
|---|---|---|---|
| Invoice+purchase.pdf p1 | invoice | 3 | genuine-mismatch×1, judge-score<0.7×1, other×2 |
| Invoice+purchase.pdf p2 | invoice | 2 | judge-score<0.7×1, other×2 |
| Invoice+purchase.pdf p3 | purchase_order | 0 | — |
| ICR.png p1 | invoice | 1 | other×1 |
| Delivery1.webp p1 | delivery_note | 0 | — |
| Invoice1.jpg p1 | invoice | 8 | genuine-mismatch×8, judge-score<0.7×1 |
| Invoice2.jpg p1 | invoice | 2 | date-format×1, genuine-mismatch×1 |
| Delivery_note2.png p1 | delivery_note | 3 | genuine-mismatch×1, judge-score<0.7×1, table-shape×2 |
| THAI_RECEIPT.jpg p1 | invoice | 14 | date-format×1, genuine-mismatch×6, judge-score<0.7×1, ocr-noise×3, other×4 |
| THAI_bill.jpg p1 | invoice | 8 | judge×1, ocr-noise×3, other×4 |
| Thai(invoice)+EN(Purchase).pdf p1 | invoice | 3 | date-format×1, genuine-mismatch×1, judge-score<0.7×1, ocr-noise×1 |
| Thai(invoice)+EN(Purchase).pdf p2 | purchase_order | 0 | — |
| purchase_orders1.pdf p1 | purchase_order | 0 | — |
| purchase_orders_2.pdf p1 | purchase_order | 0 | — |
## Examples

Quoted spans (false positives):
- (none)

OCR-noise (false positives):
- THAI_RECEIPT.jpg p1: invoice_number span='No. 5812003668'
- THAI_RECEIPT.jpg p1: bill_to_name span='สํานักงานจัดการศึกษาพิเศษ จังหวัดเชียงใหม่'
- THAI_RECEIPT.jpg p1: seller_address span='สํานักงานจัดการศึกษาพิเศษ จังหวัดเชียงใหม่'
- THAI_bill.jpg p1: line_items span='๑อเค | ชา.\nDI ชาเขียวนม sau ขนาดใหญ่ | 1 | 60.00\n- DI 0% | pees OD,\nTotal Baht) | COO'
- THAI_bill.jpg p1: line_items span='๑อเค | ชา.\nDI ชาเขียวนม sau ขนาดใหญ่ | 1 | 60.00\n- DI 0% | pees OD,\nTotal Baht) | COO'

Date-format:
- THAI_RECEIPT.jpg p1: invoice_date value='29/12/2558' span='Date: 29/12/2558'

## Top gold mismatches (model genuinely wrong)

- invoice_number: 5 pages
- seller_name: 4 pages
- seller_address: 4 pages
- cashier: 4 pages
- discount: 4 pages
- time: 4 pages
- bill_to_name: 3 pages
- bill_to_address: 3 pages
- rounding: 3 pages
- total_quantity: 3 pages

_Prevention: re-run this script after each phase (`python api/scripts/audit_review_causes.py`) — quoted-span and ocr-noise buckets must shrink while genuine-mismatch never gets reclassified as clean._
