# Refactor + repository cleanup — 2026-09-12

Clutter reduction with zero feature loss (Thai catalogs + hybrid OCR intact;
Temporal, Langfuse, providers, eval tools, browser tests, skills, and the
knowledge graph preserved; no API or DB migration).

## Deletions (with reasons)

| Removed | Reason |
|---|---|
| Tracked `.ua/.trash-1788642478/` (86 files) | Disposable analysis batches/tmp outputs from a prior knowledge-graph run |
| Tracked `.ua/intermediate/scan-result.json` | Stale intermediate scan; current graph/metadata live in `.ua/knowledge-graph.json` + `meta.json` + `fingerprints.json` (retained) |
| Tracked `eval_artifacts/metrics.partial.json` | Demonstrably superseded: its 14 page records are byte-identical to `metrics.json` → `pages` (verified in Python); complete report also carries config + summary. CI consumes `eval_report.md` + `metrics.json` + `predictions.json` only |
| Untracked `web/vite.config.js` | Generated JS copy of `vite.config.ts` from `tsc -b` |
| Untracked `web/tsconfig.tsbuildinfo`, `web/tsconfig.node.tsbuildinfo` | TypeScript build caches beside sources |

## Code changes

| Change | Reason |
|---|---|
| `api/app/agents/router.py`: removed `image_data_url()` + `base64` import | Zero code consumers (only a docs mention, also removed) |
| `api/app/services/extraction_service.py`: removed `coerce_field_dates()` + `datetime` import | Zero code consumers; Thai/Buddhist-date conversion is explicitly out of scope (unsupported dates → review) |
| `api/app/schemas/documents.py`: removed `DocumentLanguage`, `CatalogReconcileReport`, `ValidationResult`, `TemplateSchema` | Zero consumers in code, tests, routes, serialization, and docs (repo-wide grep) |
| OCR consolidation: `local_ocr.py` gains shared `is_pdf_document()` / `coerce_file_bytes()` / `load_page_images()` / `LoadedPage`; `RapidOCRClient.parse_file`/`aparse_file` delegate rendering to it; removed `RapidOCRClient._ocr_pdf_bytes` + `_is_pdf_bytes` | Single implementation of document loading, PDF rendering (PyMuPDF@DPI), Pillow decoding (EXIF/TIFF frames), and upsampling. Recognition, reading order, table gaps, timeouts, and hybrid review propagation unchanged |
| Corrected stale engine comments (`extraction_service` docstring + source note, `config.py` vision note, `POST /extract` docstring) | They named RapidOCR as the engine; Tesseract is the default, RapidOCR/hybrid are opt-in |
| `web/tsconfig.node.json`: `composite` → `noEmit`; `web/tsconfig.json`: dropped `references`; `web/package.json` build → `tsc -p tsconfig.json && tsc -p tsconfig.node.json && vite build` | Type-checking no longer emits `vite.config.js` / tsbuildinfo beside sources; `tsc -b` required composite+emit |
| `.gitignore`: added `.ua/.trash-*/` + `.ua/intermediate/` | Analysis trash cannot return as tracked changes; graph/config/metadata/fingerprints stay tracked |

## Deliberately retained (usage uncertain or still supported)

- Unused guard validators (`validate_prompt_text`, `validate_llm_response_length`, `validate_extraction_fields`, `validate_extraction_errors`) — cohesive guard-module API; removal would shrink defense-in-depth surface for no runtime gain.
- All `rapidocr`/`onnxruntime`/`opencv`/`pyclipper`/`shapely`/`pyyaml` pins — `cv2` is directly imported; the rest are required at runtime by the `rapidocr` engine family (confirmed via installed engine metadata).
- `parse_activity` (legacy text-only) alongside `parse_detailed_activity` — backward compatibility for running Temporal workers.
- `ExtractDocumentWorkflow`'s `isinstance(page, str)` branch — exercised by `test_multilingual_pages.py` mocks of the legacy activity shape.
- `data/extraction.db`, `data/sources/`, `.env` files, `logs/`, `.local/`, eval reports — never cleanup targets.
- `skills/document-extraction`, `skills/langfuse` — useful agent skills, kept.

## Docs

- `docs/local_ocr.md`, `docs/multilingual_ocr.md`: corrected pre-upgrade claims (RapidOCR is now PP-OCRv5 Thai+English, not Latin-only Thai-blind); fixed files table, config, test-deps package name, and model-pointer staleness.
- `docs/api_scripts.md`: fixed `run_eval.py` path (`api/scripts/`) and flags to match `--help`; corrected subset description; added `benchmark_ocr.py`; drew the line between `./scripts/run_all.sh` (only setup script) and `api/scripts/*` operator tools.
- `docs/evaluation.md`: linked the benchmark companion tool.
- `docs/agents.md`, `docs/services.md`: dropped dead-helper mentions; services now documents `_merge_ocr_reviews`.
- ADRs (`docs/adr/`) and `hallucination_audit_2026-09-06.md` / `hallucination_fixes_2026-09-06.md` untouched as historical records.

## Validation

- Baseline (pre-edit): `ruff` clean; `pytest`: 360 passed, 2 skipped; frontend unit: 5 passed; `npm run build` ok.
- Post-edit: `ruff` clean; `pytest`: 360 passed, 2 skipped (identical); frontend unit 5/5; `npm run build` ok with zero emitted `vite.config.js`/tsbuildinfo. HTTP contracts, OCR engine selection, page isolation, Thai metadata, and hybrid uncertainty covered by the existing suite plus `test_thai_catalog_hybrid.py` (15 tests).
- Browser test `pageResults.spec.ts` fails (timeout waiting for `input[type=file]`): the untouched, in-progress `web/src/App.tsx` rewrite (112-line active work, no file input rendered yet) cannot satisfy the spec. This refactor made zero `web/src` changes and the dev server bypasses `tsc`, so the failure is independent of this cleanup; fixing it belongs to that rewrite. Chromium was provisioned via `npm run browser:install` into `.cache/playwright` (ignored).
- Note: `web/` contains a nested git repo (`web/.git`). Its active work (`App.tsx` rewrite, `styles.css`, `package-lock.json`, `.env.example`, `vite.config.ts`) was left untouched. Verification builds churn `web/dist/*` hashed assets and `node_modules/.vite` metadata inside that nested worktree only; the root repo ignores `web/dist/`. Deleted tsbuildinfo files also register as deletions in the nested repo — removal stands per this task.

## Deliberately retained (usage uncertain or still supported)