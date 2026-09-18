# Offline rescoring of recorded developer predictions — 2026-09-15

> NOT an Iteration 2 vs 3 comparison. NOT the original run. No live inference,
> no new predictions, no thresholds invented. `eval_report.md` and
> `eval_artifacts/` are preserved unchanged.

## 1. Provenance

- Gold: `manifest.json` v1, sha256
  `589dd06f59b3eb28ac1027e18710be1cae22b62f778ad9c4dc71e57fe9eb6d4d`
  (12 files / 15 pages).
- Historical predictions: `ground_truth/eval_outputs/*.prediction.json`
  (11 files; per-file `documents`, `pages`, `eval_seconds`, `error: null`).
  Historical metrics: `eval_artifacts/metrics.json` (2026-09-11,
  `ollama-local qwen2.5:3b`, `gold_sha256 5dcbf9c0…` — an OLDER manifest
  predating `3492511_1.pdf`).
- Scoring policy: existing evaluator behavior as documented in
  `docs/reference/annotation_scoring_policy.md` (not the proposed policy).

## 2. Coverage (historical prediction set vs current inventory)

- Historical run evaluated 14/15 current pages. Missing:
  `3492511_1.pdf p1` — **not evaluated in the historical run** (file added
  later; no recorded prediction exists). This is absence of evidence, NOT a
  demonstrated processing failure.
- All 11 other files have complete per-file predictions with matching
  doc/page counts (incl. 3-page `Invoice+purchase.pdf`, 2-page Thai+EN PDF).

## 3. Historical scores (quoted, not recomputed)

From preserved `eval_artifacts/metrics.json` (14-page denominator):

| Scope | P | R | F1 | Cell | Review |
|---|---|---|---|---|---|
| Full (14 pp) | 0.444 | 0.460 | **0.445** | 0.312 | 57.1% |
| Release subset (11 pp) | 0.498 | 0.516 | **0.498** | 0.286 | 54.5% |

Routing 1.000; language 0.643; row coverage 0.639 / column 0.359.

## 4. Full-current-inventory convention (labeled)

If the 15-page current inventory is scored with missing predictions receiving
zero credit (convention stated explicitly here): the 14 scored pages keep their
recorded values and `3492511_1.pdf p1` contributes 0 (3 expected fields
unmatched). No new F1 is asserted in this report — recomputing a blended number
would mix gold versions (old `5dcbf9c0…` run vs current `589dd06f…` manifest)
and is withheld for that reason. Any future rescoring must rerun
`score_page` offline against the frozen current manifest with recorded
prediction payloads and publish the blending convention alongside.

## 5. Blocked (explicit)

1. Reproducible baseline/candidate commit IDs (historical report notes
   "uncommitted implementation"; SHA `56a24638` alone is insufficient).
2. Candidate predictions for a valid comparison (no live inference authorized).
3. PRD acceptance thresholds (no PRD found; pass bar remains "pipeline runs +
   real metrics reported").
4. Independent held-out evaluation (no instructor/new documents).
