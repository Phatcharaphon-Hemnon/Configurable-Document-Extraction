# Prompt-size vs duration calibration batch (2026-09-17, UTC 2026-09-16)

Data collection only. No timeout value derived, no production-setting
change, no second batch. Verification-only task; no code, config, History,
or production changes.

## 0. Fixed named document set (declared before dispatch)

- REUSED, not re-dispatched: `sroie_X51008142033.jpg` (prior ledger-fix
  smoke, `logs/calibration-smoke-20260916T201707-ledgerfix/`). Its run
  predates prompt_chars instrumentation, so its `extractor_prompt` is
  unknown — recorded as unknown, not backfilled.
- NEW, exactly one dispatch attempt each (6 docs, English SROIE receipts
  ordered by OCR box-text length as short/medium/long proxy):
  1. `sroie_X51005663311.jpg` (short, box 1960 chars)
  2. `sroie_X51006857265.jpg` (short, box 2153 chars)
  3. `sroie_X51005806685.jpg` (short-medium, box 2309 chars)
  4. `sroie_X51005301667.jpg` (medium, box 3494 chars)
  5. `sroie_X51006414713.jpg` (medium-long, box 5672 chars)
  6. `sroie_X51005663297.jpg` (long, box 6826 chars)
- Thai / mixed-language span: NOT satisfiable. The eval corpus holds no
  Thai receipt images (SROIE English receipts + FUNSD English forms,
  unsupported for extraction + synthetic ReportLab PDFs); the harness
  (`api/scripts/calibrate_timeouts.py`) only accepts `SROIE_ORDER`.
  No code change was made to force it. All 7 records are English.

## 1. Per-document table

prompt = `extractor_prompt` from the first extractor DispatchEvent
(chars + sha; bodies never retained). Durations: stage-block seconds
(queue + attempts + backoff); attempt/censored = per-HTTP transport.

| filename | extractor_prompt_chars (sha) | outcome | router ok (s) | extractor censored (s) | ocr / router / extractor / judge stage (s) | total (s) |
|---|---|---|---|---|---|---|
| sroie_X51008142033.jpg (reused) | unknown (pre-instrumentation) | partial — extractor timeout | 101.73 | 300.10 | 3.57 / 101.74 / 300.14 / 0.0 not_run | 406.66 |
| sroie_X51005663311.jpg | 8090 (5f1171e6bc08) | partial — extractor timeout | 111.68 | 300.10 | 4.74 / 111.69 / 300.15 / 0.0 not_run | 417.76 |
| sroie_X51006857265.jpg | 8107 (5602f8de918c) | partial — extractor timeout | 98.12 | 300.10 | 4.99 / 98.13 / 300.15 / 0.0 not_run | 404.77 |
| sroie_X51005806685.jpg | 7985 (ffdf6013e499) | partial — extractor timeout | 86.60 | 300.10 | 7.49 / 86.61 / 300.14 / 0.0 not_run | 397.51 |
| sroie_X51005301667.jpg | 8506 (dddee4d5a2e5) | partial — extractor timeout | 119.70 | 300.01 | 11.26 / 119.70 / 300.07 / 0.0 not_run | 433.41 |
| sroie_X51006414713.jpg | 8697 (0f2eddbbbb55) | partial — extractor timeout | 143.51 | 300.10 | 13.22 / 143.52 / 300.16 / 0.0 not_run | 459.15 |
| sroie_X51005663297.jpg | 9035 (3e200119f846) | partial — extractor timeout | 186.57 | 300.10 | 11.54 / 186.58 / 300.15 / 0.0 not_run | 499.59 |

Every new run: `dispatches 2, dispatches_known 2, dispatches_uncertain 0,
intents 2, terminal {ok: 1, timeout: 1}`, `timeouts 1, errors 0`,
`judge_status unavailable`, `confirm []`, `applied {false, "not requested"}`.
No run dispatched the judge (stopped after the first inference timeout).

## 2. Visible pattern (observables only)

- Extractor prompt size is nearly flat: 7985–9035 chars (~13% spread)
  while OCR box text spans 3.5x (1960–6826 chars). Observable implication
  only: the extractor prompt is dominated by fixed overhead
  (catalog/schema/contract), not document text, in this sample.
- Extractor outcome is invariant: all 6 new attempts censored at the 300s
  transport ceiling, zero completed extractor samples. With no completed
  extractor durations, **no prompt_chars → duration correlation is
  observable for the extractor in this sample**.
- Router durations rise roughly with document length (86.6s → 186.6s
  across the box-size order, with noise: the shortest doc took 111.7s,
  third-shortest 86.6s), but router prompt size is NOT instrumented
  (extractor-only by design), so no prompt→duration statement about the
  router is supported by this data either.
- Explicit caveat: n=6 (+1 reused without prompt data), single provider
  (`qwen2.5:3b` local CPU), single ceiling (300s), all-extractor-timeout.
  **The sample is too small and too censored to conclude anything about
  prompt-size/duration correlation.** No calibration decision follows.

## 3. Persistence and measurement-fidelity outcome: PASS (all 6 new docs)

- Start saved pre-inference: yes — every `progress.jsonl` line 1 is
  `doc_start` for its document, line 2 `doc_outcome` (2 lines each).
- Dispatch events survive nested collectors: yes — 2/2 known, 0
  uncertain, intents 2, terminal-by-outcome {ok:1, timeout:1} on all 6.
- Durations from the actual emitted contract: yes — top-level `timings`,
  all stages `measured` except judge `0.0 / not_run` (provably never ran).
- Unknown=null never fabricated: no unknown stage on these paths; the
  reused record's missing prompt fingerprint stays unknown, not zero.
- Timeout observations censored: yes — extractor 300.0–300.1s under
  `censored_durations`, never merged into completed samples (which are
  empty → extractor p95 withheld, `candidates.extractor null`).
- Unattributed stage errors stay unattributed: same returned-failure path
  as the prior smoke (`failed_stage` extractor, `partial`); no raised-path
  ambiguity on any of the 6 runs.
- Counts/durations agree with transport evidence: per-doc stderr shows
  exactly one `stage=extractor ... category=timeout` pair per run;
  stage-block vs transport magnitudes coherent (e.g. router block 111.69
  vs transport 111.68). (`logger.info` prompt_chars lines are below the
  harness stderr level; the event data path in `measurements.json` is the
  verified carrier — present on all 6.)

## 4. Bounds compliance (per-document, all met; batch ceiling met)

| Bound | Observed |
|---|---|
| ≤300s per inference attempt | router ok 86.6–186.6s; extractor 300.0–300.1s hit ceiling = first inference timeout, doc stopped |
| ≤600s document processing | totals 397.51–499.59s |
| ≤660s wall per doc incl. preflight + cleanup | each `--only` run exit 0 inside outer `timeout 660s` |
| ≤3 inference HTTP dispatches per doc | exactly 2 each (router + extractor; judge never dispatched) |
| No retries/fallbacks/corrections/confirm/apply | single-attempt patch; `confirm []`, `applied false "not requested"` |
| Stop after first inference timeout | extractor attempt 1 timeout → `partial`, no further dispatch, on all 6 |
| Stop before dispatch if idleness unprovable | before each doc: `/api/ps {"models":[]}`, `ollama ps` empty, History `queued/processing []`, tags show `qwen2.5:3b` (no pull); post-timeout model residency drained (slept until expiry) before next dispatch |
| Batch hard ceiling 90 min | 20260916T212009 → 20260916T223434 ≈ 74.4 min for all 6 |

## 5. Checks executed (real output)

- `ruff check api/` → All checks passed.
- Focused: `test_prompt_size` → 4 passed; 6-file calibration/observability
  set → 72 passed.
- Full backend `python -m pytest api/tests/ -q` → 715 passed, 2 skipped
  (pre-existing skips).
- Frontend build: not run — no UI changed (per verify rule).
- `api/.env` byte-identical before/after
  (`sha256 7942bfbdc924bf87…`, `git status` clean).
- Historical artifacts preserved: `/tmp/calib-verify-20260916T000000-smoke/`,
  `/tmp/calib-verify-20260916T201707-ledgerfix-smoke/`,
  `logs/calibration-smoke-20260916T201707-ledgerfix/` untouched.
- New isolated dirs per doc: `/tmp/calib-promptsize-20260917-<id>/`
  (3 harness files + stderr each) with sanitized copies under
  `logs/calibration-promptsize-20260917-<id>/`. No lingering harness
  process post-batch.
