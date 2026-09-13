# Implementation report — accuracy, persistent cache, latency

## Build & config identifiers

- Base commit: `0bbdfe7` (working tree carries prior unstaged work + this
  submission, uncommitted per instructions).
- Provider/model (unchanged): `LLM_PROVIDER=ollama-local`,
  `LLM_MODEL=qwen2.5:3b` (local Ollama, CPU). Concurrency 1, shared
  four-attempt retry budget — preserved.
- OCR: `tesseract` default, `eng+tha`, DPI 300; hybrid stays opt-in.
- Flags: `JUDGE_SKIP_WHEN_CLEAN=true/0.85`, `FEW_SHOT=0`, reasoning disabled,
  `RESULT_CACHE_ENABLED=true` (new, gated by the tests below).
- Hardware/runtime: 8 CPU, 7 GB RAM, Python 3.14.7, onnxruntime 1.29.0,
  Ollama local (`qwen2.5:3b` 1.9 GB). Models loaded warm for OCR; LLM warm via
  Ollama daemon. Cold-start (process/OCR-model import) not separately timed —
  reported as limitation.

## Reference provenance & verification status

- Real refs preserved: `data/regression_refs/` (bytes + `annotations.json`
  with SHA-256, dimensions, page order, screenshot linkage) + full backup
  `data/backups/20260912T053413-pre-implementation/` (canonical.db, sources/,
  saved outputs per job). Runtime History reset executed AFTER preservation
  (see below).
- Gold manifest: v1, 12 files; 3492511 case scores 3 confident fields only
  (date/seller/currency/amounts excluded as ambiguous). Annotations in this
  submission are assistant-generated and PROVISIONAL (not independently
  human-adjudicated). Unit tests use synthetic fixtures only.
- Missing: 5 UUID PNG attachments (ENOENT, path absent) — requested implicitly
  by reporting; pixel-check of those 5 not done.

## What changed (code)

- Typed calls: `ExtractionCallResult` replaces `last_tables`; explicit
  page-bound passing local + Temporal (`extractors.py`, `activities.py`,
  `workflows.py` unchanged shape).
- Evidence: stable block IDs, backend-resolved `EvidenceReference`
  (label/value/context), multiline support (`evidence.py`).
- Acceptance v1.0.0 (`acceptance.py`, `validator.py` front): labels≠values,
  type enforcement, explicit currency, role windows, zeros/leading-zeros kept,
  ambiguous→unresolved, coverage from accepted only, positional table keys, no
  elsewhere-fallback, no placeholder rows, no totals-in-items, no scalar/table
  duplicates.
- Judge: structured issues + `reconcile_judge_issues` + strengthened
  `should_skip_judge` (confidence necessary, never sufficient).
- Cache: `result_cache.py` (SQLite, TTL 7d, 128 entries, fingerprint without
  secrets, atomic/evicting/validating), lookup before provider queue,
  per-page partial caching, `force_refresh` (result-only bypass) +
  `disable_caches` (both), single-flight kept. `clear_history` also purges
  cache entries.
- Perf: shared `AsyncOpenAI` transports, unsupported-tier memory (explicit 400s
  only, never malformed output), per-request OCR bypass, no duplicate
  scalar/table output.
- UI: confidence labeled model estimate; Data badges
  (accepted/needs-review/unresolved/legacy-unevaluated); rejected + structured
  issues sections; auto-eval shown as measured accuracy separate from review;
  stepper labeled execution-only; skipped/unavailable never "passed"; column
  counts from rendered structure; legacy fallback filtered + labeled; filter
  resets per page; Force-refresh button.

## Verification

- Backend: `ruff check api/` clean; `pytest api/tests/` **395 passed,
  2 skipped** (baseline 372+2; +23 new: acceptance 9, cache 9, isolation 4,
  +1 evidence update). Slow client suites included and passing.
- Frontend: `npm run build` clean; `npm test` 5/5.
- Cache tests: roundtrip/persistence, TTL/eviction, catalog invalidation,
  corrupt handling, force-refresh, concurrent duplicates, partial pages,
  source remapping (new IDs/URLs, original timings in metadata), non-cacheable
  (error/unavailable-Judge) exclusion, clear + active-job guard.
- Isolation: `test_history_isolation` passes (synthetic `scan.png` never
  touches runtime History).
- Browser scenarios (uploads, incremental pages, review, exports, force
  refresh, History clearing) NOT run — no browser harness in this session;
  reported as blocked verification, not substituted.

## Measured outcomes (whole-submission targets)

Historical warm-model runs (saved outputs, OCR-cache hits, models as then
configured — screenshots show mixed `gpt-oss:20b`/`qwen2.5:3b`; exact
per-stage model for these rows not recorded — limitation):

| Submission | Pages | Upload→final (server `processing`) | Per-page pipeline | Router | Extractor | Judge | Queue |
|---|---|---|---|---|---|---|---|
| 3492511_1.jpg | 1 | 488.5 s | 488.3 s | 48.5 s | 345.4 s | 93.1 s | 0.0 s |
| invoice_form.webp | 1 | 616.5 s | 616.4 s | 72.2 s | 320.1 s | 222.5 s | 477.9 s |
| Invoice+purchase.pdf | 3 | (see saved output; mixed th/en, all flagged) | — | — | — | — | — |

OCR bench today (isolated cache, `use_cache=False`, Tesseract eng+tha):

| File | Cold OCR+render | Coherence | Outcome |
|---|---|---|---|
| 3492511_1.jpg p1 | 7.87 s (ocr 5.72 + render 2.04) | 0.409 → passes gate (was 0.35 on the PDF render in prior docs) | Salad preserved w/ 27 blocks; new policy rejects date/currency/placeholders instead of accepting |
| invoice_form.webp p1 | 3.78 s | 0.759 pass | — |
| Invoice+purchase.pdf p1/p2/p3 | 15.80 s total (6.28/5.60/1.82 + renders) | 0.53/0.62/0.62 pass | — |

Cache-mechanism (isolated, synthetic payloads, real SQLite code): put→get
roundtrip hit; second identical `extract_group` reuses without new extractor
calls; accepted/rejected/evidence parity holds excluding IDs/URLs/timings;
force-refresh re-runs; corrupt/expired → safe miss; catalog edit → new
fingerprint.

## Target status

- 1–3 pages < 60 s new files: **MISSED** on this deployment. Bottleneck is LLM
  decode (~2.8 tok/s measured previously; 320–345 s extractor + 48–72 s router
  + 93–222 s judge per page). No timeout was imposed; improvements (shared
  transports, tier memory, duplicate-output removal, skip gate) save seconds,
  not the ~10× needed. Needs a faster model/endpoint or scoped output cuts
  (not applied — would risk dropping rows/evidence).
- Repeated files < 5 s: **MET for the mechanism** (cache-hit path does no LLM;
  measured as zero new extractor calls + ms lookup in tests), **not measured
  live end-to-end** on the server in this session.
- 100% precision/recall on readable refs: **UNVERIFIED** (no live LLM rerun;
  no claim made). Deterministic gates now reject the screenshot failure modes
  by construction (tests), but measured accuracy requires a warm-model eval
  rerun (`run_eval.py --all`) with the new policy.
- Ambiguous handwriting: reported separately (3 confident fields scored;
  date/seller/currency/amounts excluded; handwriting stays unresolved).

## Accuracy-adjacent counts (deterministic, synthetic)

See `test_acceptance.py` (9 tests): 3/3 blank labels rejected; THB accepted
iff printed; date/amount/currency/role rejections; zeros/`000123` kept;
duplicate columns positionalized; placeholder/wrong-row/invalid tables
rejected; legacy loads `unevaluated`. Judge reconciliation: false mechanical
discarded, semantic kept, duplicates merged (`test_page_isolation.py`).

## History reset & cleanup

- Preconditions verified: 0 active jobs; backup written + integrity ok;
  regression bytes/outputs/hashes/annotations preserved outside prompts,
  examples, retrieval, and catalogs.
- Executed: `DELETE /api/history` on the live backend → jobs/sources cleared;
  stats zeroed; pagination/selection reset (UI already confirms + surfaces
  409 while active). Result-cache purge included in `clear_history` (no live
  entries existed under the old server binary).
- Post-reset: History starts empty; subsequent real uploads nest pages under
  one job with original filenames; previews create no jobs (covered by
  `test_history_clear.py` + `test_history_isolation.py`).
- Cleanup limited to: already-staged `.ua/.trash` removals (prior work, left
  staged) — no additional files deleted; no dead-code removal beyond the
  `last_tables` replacement; docs added only.

## Limitations & remaining blockers

1. Sub-minute extraction impossible on `qwen2.5:3b`/CPU here; next step is a
   faster endpoint or profiled prompt/output reduction (must not truncate).
2. Live full-pipeline rerun + `run_eval --all` + browser scenarios still
   required to convert deterministic fixes into measured accuracy/latency.
3. Table-leak first-layer attribution (E1/E2) needs an instrumented warm-model
   3-page rerun.
4. OCR disk-cache warm-hit anomaly in the isolated bench (`cached=False` on
   immediate reload) needs a follow-up look (runtime cache historically hits).

## Rollback

- Backend: revert the files listed in "What changed"; set
  `RESULT_CACHE_ENABLED=false` to disable caching without a revert; delete
  `.cache/result-cache.sqlite` to purge. Fingerprint versions auto-invalidate
  across the boundary.
- Frontend: revert `ExtractionTab`/`types`/`client`/`useDocumentQueue`; old
  payloads validate (new fields optional, legacy `unevaluated`).
- Data: restore `data/backups/20260912T053413-pre-implementation/canonical.db`
  + `sources/` if the reset must be undone (procedure in `docs/…`; integrity
  was ok at backup).
