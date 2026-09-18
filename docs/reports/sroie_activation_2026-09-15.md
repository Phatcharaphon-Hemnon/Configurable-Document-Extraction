# SROIE-8 activation report — 2026-09-15 (branch perf/latency-opt-20260914)

> Active dataset replaced: 8 SROIE documents. Mixed 20-target NOT complete
> (DocILE 0/8 blocked — token; Thai 0/4 blocked — Roboflow auth).
> No live inference, model downloads, History changes, or pushes.

## 1. Scope accounting (actual)

- Supported references: **24** (8 pages × seller_name/seller_address/total_amount).
- Unsupported references: **8** (`sroie_receipt_date`, recorded per page, never FN).
- `sroie_receipt_date` is unfillable by the unmodified production pipeline
  (verified: pipeline emits `invoice_date`, which scores out-of-scope).
- Router accuracy: N/A by design (all pages extraction-only).
- Model accuracy/latency: **not evaluated**. No historical scores transferred.

## 2. Added paths (active `ground_truth/`)

8× `sroie_<id>.jpg` + 8× `.box.txt` + 8× `.entities.json` (bytes unchanged from
`/home/phatcharaphon/dataset/SROIE2019/train`), `manifest.json` v2,
`review_package_sroie.json` (8 pending entries), preserved `review_viewer.html`.
IDs: X51005301667, X51005663293, X51005663297, X51005806685, X51006414713,
X51006556815, X51006857265, X51008123604 (seed 20260915).

## 3. Deleted paths (retired, authorized)

- 12 old sources (3492511_1.pdf, Delivery1.webp, Delivery_note2.png, ICR.png,
  Invoice+purchase.pdf, Invoice1.jpg, Invoice2.jpg, THAI_RECEIPT.jpg,
  THAI_bill.jpg, Thai(invoice)+EN(Purchase).pdf, purchase_orders1.pdf,
  purchase_orders_2.pdf) + 11 `eval_outputs/*.prediction.json`.
- Old `review_package.json`, `proposed_corrections.json` (untracked; new
  `review_package_sroie.json` has a distinct filename and was never at risk).
- Root `eval_report.md`, `eval_artifacts/metrics.json`, `eval_artifacts/predictions.json`.
- Historical `docs/reports/*` preserved as history (no stale score presented
  as current; guides updated).

## 4. Code/test/doc changes

- `run_eval.py`: `DEFAULT_SUBSET` → 8 SROIE names; `DEFAULT_GOLD_DIR` mechanism
  unchanged (same dir, new content); scoped/alias/N/A scoring from prior work.
- `evaluation.py`: `unsupported_fields` (optional, legacy-compatible).
- `import_sroie.py`: 3-field scope + unsupported date + entities preservation.
- Tests: `test_gold_audit.py` rewritten (8/8, 24+8, artifacts, no-invoice_date);
  `test_run_eval.py` subset pin; `test_history_isolation.py` synthetic fixture;
  `test_catalog_required_alignment.py` explicit scope-limited exemption
  (catalogs untouched); `test_mixed_suite.py` +2 (default-resolves-SROIE,
  isolated verify-fail-restore, mock-redirect-never-writes-active).
- Docs: `guides/evaluation.md`, `gold_review_viewer.md`, `annotation_scoring_policy.md`,
  `rag_kb.md` updated; this report added.

## 5. Verification outcomes

- Staging re-validated; disposable-copy `--mock` run: 8/8 discovered, reports
  in /tmp only, active tree untouched during validation.
- Post-activation: default discovers exactly 8 images; all hashes/paths/scopes
  validate; `review_package_sroie.json` images all exist, manifest sha matches;
  viewer JS `node --check` clean (interactive load blocked: no browser tooling).
- Tests: targeted 41/41 → full suite **559 passed, 2 skipped, 1 failed** —
  the failure is pre-existing `test_region_pipeline.py` (untracked third-party
  file, region-dispatch logic untouched by this task; traceback captured in
  audit). Ruff clean on all touched files.
- Runtime `data-local` (db/sources/cache/logs) and unrelated dirty files
  verified untouched (mtimes predate session; dirty set identical to snapshot).

## 6. Blocked checks

DocILE token, Roboflow export, browser-interactive viewer load, live model
metrics. Line-item adapter-vs-blocked decision awaits real DocILE data.

## 7. Rollback

- Backup: `/tmp/opencode/pre-activation-20260915/` (old_gold/ 26 files incl.
  eval_outputs, old_eval/ report+metrics, rollback_active/ moved active tree)
  — hash-verified at backup time (15/15).
- Tracked deletions: `git checkout -- <path>` / `git reset`; untracked review
  JSONs + new SROIE assets: restore/delete per §3/§2 lists. Never `git reset`
  broadly (unrelated dirty work must survive).
