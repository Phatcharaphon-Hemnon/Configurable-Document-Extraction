# SROIE expansion 8 → 10 — implementation record 2026-09-15

> Incremental selection, not a fresh sample. Active suite: 10 SROIE documents.
> Composition target remains 10 SROIE + 10 DocILE (active 10/20; DocILE 0/10 blocked).
> Manifest schema version unchanged (v2). No live inference, History changes, or pushes.

## 1. Incremental selection (seed 20260915, existing 8 excluded)

Pool: same verified 626-ID train pool. `select_ids(pool − active8, 2, 20260915)` →
`X51005663311`, `X51008142033` (reproduced by re-execution; recorded here, not resampled).
Visually inspected against labels: LIM SENG THO HARDWARE TRADING, 09/02/2018,
total 7.00; ONE ONE THREE SEAFOOD RESTAURANT SDN BHD, 19-05-2018, total 42.40.

## 2. Scope totals

Preserved 4 labels per entities JSON; production scope 3 fields/page →
**30 supported + 10 unsupported** (`sroie_receipt_date`, never FN, unfillable
by the unmodified pipeline — verified end to end).

## 3. Activation

Pre-change: existing 8 proven byte-identical in active and final dirs.
Copied into active `ground_truth/`: 2× (jpg + box.txt + entities.json),
updated `manifest.json` (v2, `release_subset` = 10), regenerated
`review_package_sroie.json` (10 entries, sha-bound). Post-change: default
discovers exactly 10 images; all hashes/paths/scopes validate; review package
bound (10/10, no missing images). No deletion of the prior 8; DocILE/Thai
staging untouched (both empty).

## 4. Bug found by validation

`manifest_builder.build_combined_manifest` hardcoded `release_subset: []`,
so a mock run discovered 0 pages. Fixed with an explicit `release_subset`
parameter (default: all files) and re-validated: disposable-copy `--mock`
run → 10/10 pages. All mock writes confined to /tmp.

## 5. Tests/docs

`DEFAULT_SUBSET` → 10 names; pins updated (`test_gold_audit` 10/30/10,
`test_run_eval` 10, catalog exemption 10, `test_mixed_suite` default-resolves-10).
Guides updated to 10-file suite. Full gate below.

## 6. DocILE access requirement (verified against ~/docile checkout)

- Access link: **https://docile.rossum.ai/** (token instructions there; never
  paste tokens into chat or reports).
- Exact command (verified against `download_dataset.sh`: positional
  `SECRET_TOKEN DATASET TARGET_DIR [--unzip]`; dataset name passes through
  to `<name>.zip`):
  `./download_dataset.sh <TOKEN> annotated-trainval data-local/mixed_suite_staging/docile/ --unzip`
  (README's name is `annotated-trainval`; the script help text says
  `labeled-trainval` — if the first 404s/fails size-check, resolve the right
  name with `./download_dataset.sh <TOKEN> annotated-trainval --show-urls`
  which prints the URL without downloading, and report back which name works).
- Expected layout under `data-local/mixed_suite_staging/docile/`: the unzipped
  trainval tree with a split index file, per-document annotation JSONs
  (`field_extractions`/`line_item_extractions`), and PDFs. Minimum usable:
  10+ real annotated documents. Do NOT supply `synthetic/`, `unlabeled/`,
  or `test/` (quota-ineligible). I will verify index + annotation files,
  select 10 with seed 20260915, and append — fixtures are never counted.
