# Mixed 20-document suite — implementation report 2026-09-15

> Quotas incomplete (8/20 imported). The old 12-file/15-page suite stays
> active: `DEFAULT_GOLD_DIR` unchanged, no deletion, no activation.
> No live inference, model downloads, History changes, or pushes.

## 1. Readiness ledger (four separate counts)

| Source | Imported / quota | Text-scoreable | Line-item-scoreable | Localization-only |
|---|---|---|---|---|
| SROIE | 8 / 8 | 8 | 0 | 0 |
| DocILE | 0 / 8 (blocked) | 0 | 0 (adapter or blocked, pending data) | 0 |
| Thai Receipt | 0 / 4 (blocked) | 0 | 0 | 0 |
| **Total** | **8 / 20 — NOT complete** | 8 | 0 | 0 |

Staging manifest `data-local/mixed_suite_staging/combined_manifest.json`
(version 2, validates against extended `GoldManifest`) records
`complete: false`. The 8 staged SROIE pages are extraction-only
(`routing_excluded: true`) with 4-field scopes
(`seller_name, seller_address, total_amount, sroie_receipt_date`).

## 2. Access outcomes (observed, not assumed)

- **SROIE — available.** Verified local copy `/home/phatcharaphon/dataset/SROIE2019`
  (LayoutLM-structured third-party copy: 626/626/626 train img/entities/box
  triples, 347 test pairs; more than the official 600 — recorded in provenance).
  Mirror repo reachable (GitHub API 200); official RRC page unreachable from
  here (TLS verify failure) — not needed since the local copy validates.
  Selection: seed 20260915 over sorted train IDs →
  `X51005301667, X51005663293, X51005663297, X51005806685, X51006414713,
  X51006556815, X51006857265, X51008123604`. Invalid/skipped: none.
- **DocILE — blocked.** No token and no local copy found (searched
  `dataset/`, `data-local/`, repo tree). Requires a token from
  `https://docile.rossum.ai/` (`download_dataset.sh TOKEN annotated-trainval`).
  Importer + fixture tests implemented; synthetic/unlabeled/test splits are
  rejected by design; no fixture counted as an import.
- **Thai Receipt — blocked.** Observed: Universe dataset page 403 and
  `api.roboflow.com` 401 without credentials. Requires a Roboflow account +
  dataset export. Importer (COCO/YOLO shapes), augmented-copy dedupe, and
  localization-only path implemented and fixture-tested; transcription
  scoring stays unavailable until text-value labels are inspected.

## 3. What was implemented

- `api/scripts/mixed_suite/`: `storage.py` (symlink-resolved isolation vs all
  effective paths), `select.py` (seeded deterministic draw), `import_sroie.py`
  (eval-local mapping incl. deliberate `date→sroie_receipt_date`, never
  `invoice_date`; catalogs untouched), `import_docile.py` (blocked report,
  split locator, grouping-preserving `adapt_fields`), `import_thai.py`
  (blocked report, `dedupe_originals`, `localization_only_page`),
  `manifest_builder.py` (hash verification, duplicate detection, readiness).
- `app/schemas/evaluation.py`: optional `DatasetProvenance`, `annotation_scope`,
  `routing_excluded` (legacy manifests validate unchanged).
- `api/scripts/run_eval.py`: `DEFAULT_GOLD_DIR` constant (activation target;
  still the old suite), symmetric aliases with collision errors, scope-driven
  TP/FP/FN (failed docs: TP=0/FP=0/FN=all in-scope; 0/0→1.0 iff nothing
  scoreable), router N/A when all excluded, `scoreable`-aware headline
  (`macro_f1_scoreable`, `n_empty_scope`), TP/FP/FN + coverage totals in
  summary and report.
- `review_viewer.html`: dataset filter, provenance/scope/extraction-only
  badges, original-vs-mapped labels, boxes, unsupported-scope rows
  (text-only rendering, validation-before-apply preserved).
- `api/tests/test_mixed_suite.py`: 16 tests (isolation, selection,
  3 importers, builder guards, scoped-scoring quartet, aliases, empty-scope
  headline, default-resolution, catalog tripwire).

## 4. Deliberately not done (blocked scope)

- DocILE line-item adapter-vs-blocked decision needs real annotations;
  current rule: geometry/official matching or blocked, never gold-aligned.
- Default-dataset activation: requires 20/20 validation at final paths, then
  copy → verify → switch `DEFAULT_GOLD_DIR` → verify → delete old per
  inventory. Rollback before activation = drop staging (one command);
  after activation = restore pointer + backed-up legacy assets.
- Old-suite test pins (`test_gold_audit.py` 12/15/8) kept: the old suite is
  still the active default and must stay pinned until activation.

## 5. Verification

- New: 16/16 pass. Related: `test_run_eval.py` + `test_gold_audit.py` pass
  (legacy behavior preserved). Ruff clean on all touched files.
- Staged SROIE assets: 8 images + 8 box files + manifest in ignored
  `data-local/mixed_suite_staging/` (active KB untouched; catalog hashes
  unchanged).
