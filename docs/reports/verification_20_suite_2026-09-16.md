# Verification report — 20-document suite (SROIE 10 + FUNSD 10), 2026-09-16

Branch `perf/latency-opt-20260914`. No pushes, History changes, live
inference, downloads, or service restarts. Unrelated working-tree changes,
catalogs, credentials, models, and all 20 active source documents preserved.

## 1. Region-test root cause (fixed, with evidence)

`test_dispatch_ceilings_stop_with_explicit_error` failed because NO ceiling
sections ever reached progress — two compounding production defects, found by
line-tracing `_extract_page_with_regions` with the test's own fixture:

1. **Double debit**: an empty-result region debited its attempts on the
   success path (`used`, then `ValueError` on empty output), and the except
   handler debited the same attempts again. One 4-attempt call consumed 8
   dispatch budget (observed `dispatches=8` after a single region call),
   tripping the ceiling a region early and inflating all dispatch accounting.
2. **Unpublished ceiling sections**: the ceiling branch appended
   failed/unresolved sections then `break` skipped the per-iteration
   `set_progress`, so polling never saw them (observed 1 section instead of 4).

Fix (`api/app/services/extraction_service.py`, minimal): except handler debits
only the undebited remainder (`extra = max(0, used - debited)`); ceiling
branch publishes progress before `break`. No ceiling raised, no assertion
weakened, no skips. New regression test `test_empty_region_debits_budget_exactly_once`
(4-used-per-call ⇒ dispatches == calls × 4). "Pre-existing" attribution for the
old failure: none claimed — the file is untracked third-party work, but the
defect was confirmed present-tense by trace and fixed at its cause.

## 2. HTTP-boundary budget proof (mocked transport, real SDK)

New optional seam `Client(settings, http_client=None)` (default path
byte-identical: shared pool, `max_retries=0`). New `test_http_budget.py`
drives a real `AsyncOpenAI` client over `httpx.MockTransport`: SDK retries
asserted disabled; 429×2→200 yields exactly 3 HTTP dispatches;
malformed→valid corrective path 2 dispatches; persistent 429s capped at
exactly 4; `last_attempts` equals counted dispatches in all cases.
4/4 pass. This counts outbound HTTP requests, not SDK method calls.

## 3. Interactive viewer verification (51/51, isolated)

Disposable `/tmp/opencode/viewer-check` (viewer + package + 62 asset files
only), served `python3 -m http.server 8901 --bind 127.0.0.1 --directory …`,
Playwright (vendored chromium-1243, no downloads). All 20 images opened in
the browser: JPEG SOI / PNG magic per actual format, successful decode,
natural dimensions equal PIL ground truth (incl. 4961×7016). New minimal
viewer support (same file, textContent-only): image pane, FUNSD box overlays
from sidecars, click-to-trace links with unique-vs-repeated counts
(verified: 23 unique edges incl. ×2 repeats on doc 0001118259), SROIE
unsupported-date badges, pending-by-default statuses, export/import
round-trip with hash/state validation, conflicting imports rejected visibly
(a real log-clobbering bug in `applyDecisions` found and fixed during this).
No page errors. Statuses unchanged; temp exports deleted; server stopped.

## 4. Evaluator recheck (real code, disposable copies)

Mock run on a copy of the active suite: 20 discovered; 10 evaluated +
10 not_evaluated; TP/FP/FN 20/0/10 over 30 supported refs (mock drops one
in-scope field per page by design; the 10 unsupported dates contribute 0 FN);
router N/A (None, 0 evaluated / 10 excluded); per-dataset/kind sections
separate. Active suite gained no synthetic outputs (verified absent).
Leakage tripwires green. Mixed-file limitation, default/explicit `--gold-dir`,
and zero-call FUNSD guard covered by tests.

## 5. Prepared real-evaluation command (NOT executed)

```
PROJECT_CACHE_DIR=/tmp/opencode/real-eval-2/cache \
.venv/bin/python api/scripts/run_eval.py \
  --gold-dir /tmp/opencode/real-eval-2/gold \
  --subset sroie_X51005301667.jpg sroie_X51005663293.jpg \
  --few-shot 0 --output-dir /tmp/opencode/real-eval-2/out
```

All flags verified against `--help`; effective config recorded without
secrets (provider ollama-local, model qwen2.5:3b, tesseract eng+tha 300 DPI,
concurrency 1, `--gold-dir` writes predictions so a disposable copy is
mandatory). Live metrics: not measured. No `run_all.sh` (manages services).

## 6. Gates + deliverables

- `ruff check api/`: clean (also removed 3 pre-existing unused imports in
  the region test file).
- Full backend suite: **569 passed, 2 skipped, 0 failed**.
- Frontend untouched (viewer is static HTML under api data; no web/ changes).
- Changed: `extraction_service.py` (2 fixes), `client.py` (seam),
  `review_viewer.html` (image/overlay/tracing + log fix), new
  `test_http_budget.py`, region regression test, activation/test/doc updates
  from the 20-suite work. Rollback: task paths only (`git checkout --` the
  listed files + remove added files); backups in `/tmp/opencode/`.
- Limitations: browser check covered Chromium only; FUNSD production metrics
  remain not-evaluated by design; DocILE/Thai out of quota.
