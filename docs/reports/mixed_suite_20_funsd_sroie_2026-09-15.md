# Mixed 20-document suite (10 SROIE + 10 FUNSD) — implementation report 2026-09-15

> Active suite: 20 source documents, manifest v2 (schema version unchanged).
> DocILE/Thai removed from quota. No live inference, downloads (beyond the
> supplied local copies), History changes, or pushes.

## 1. Import completeness vs evaluation coverage (separate)

- 20 source documents: 10 SROIE + 10 FUNSD (hash-verified, pairing-exact).
- SROIE: 30 supported field refs + 10 unsupported date refs (production-comparable).
- FUNSD: 509 entities / 307 explicit links / 92 unlinked recorded across the
  10 selected docs (0 ambiguous); **production task metrics not evaluated**
  (dispatch guard returns `not_evaluated` before any pipeline call).
- This is NOT 20 production-extraction-evaluable documents.

## 2. FUNSD selection (seed 20260915, verified pool)

Source (corrected path): `/home/phatcharaphon/Downloads/dataset/training_data`
(149 complete pairs; originals unchanged). Selected:
`0001118259, 0011973451, 0011974919, 00283813, 0060207528, 01197604,
71108371, 87533049, 91361993, 93380187` (0 invalid). Per-doc entity/link/
unlinked counts recorded in derived sidecars (selected docs: 509 entities,
307 links, 92 unlinked, 0 ambiguous). Corpus quirk handled: links stored as
`[src, tgt]` pairs (initial misparse caught and fixed; 4230 corpus links,
0 ambiguous after fix). Link endpoints validated; repeats flagged with counts.

## 3. Contract (evaluation-only, production Literal untouched)

`GoldPage`: `doc_type` Optional, `document_kind` (`"form"`), null
`production_doc_type`, `annotation_refs` (active-path bound), `AnnotationRef` /
`GoldEntity` / `GoldLink` / `DerivedAnnotations` models, conflict-rejecting
validator, documented additive version policy. Legacy manifests load with
equivalent meaning. FUNSD stored under no production type (rejected invoice
carrier per instruction).

## 4. Dispatch guard + report plumbing

`file_support()` diverts unsupported (incl. mixed-kind, explicit limitation)
files before mock fabrication, `extract_group`, warm OCR, and catalog contact;
`not_evaluated` records carry kind/reason with zero TP/FP/FN; aggregates count
`n_evaluated/n_unevaluated` separately; per-page rows show NOT EVALUATED with
reason; per-kind/per-source breakdowns added. Verified on disposable copy:
10 evaluated + 10 not_evaluated, TP/FP/FN 20/0/10 over evaluated refs.

## 5. Changed files

- New: `import_funsd.py`, 30 FUNSD assets (10 png/json/derived) in active
  `ground_truth/`, `review_package_funsd_sroie.json` (20 pending entries).
- Modified: `evaluation.py`, `run_eval.py` (guard, N/A plumbing, 20-name
  subset), `manifest_builder.py` (release_subset param), `import_sroie.py`
  (entities copy), `import_docile.py` (quota note), `manifest.json` (20 files),
  `review_viewer.html` (kind badge), tests (`gold_audit`, `mixed_suite`,
  `run_eval`, `catalog_required_alignment`), guides (`evaluation`,
  `gold_review_viewer`, `rag_kb`).
- Prior SROIE 10 proven byte-identical before/after; runtime `data-local`
  (db/sources/cache/logs) and unrelated dirt untouched.

## 6. Tests run

New/updated: kind matrix, zero-call guard via real `run()`, mixed-file
limitation, sidecar link validation, final-path ref resolution,
staging-unavailable pattern (refs assert no staging/tmp substrings),
20-file pins, 30+10 counts. Full gate: **563 passed, 2 skipped, 1 failed**
(pre-existing `test_region_pipeline.py`, untracked file, untouched logic).
Ruff clean on all touched files. Blocked: browser-interactive viewer load,
live model metrics, DocILE/Thai (out of quota).
