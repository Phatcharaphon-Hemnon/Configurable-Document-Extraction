# Extractor 1200s-ceiling diagnostic — single new dispatch (2026-09-17)

One authorized document execution: `sroie_X51008142033.jpg` (reuse-eligible,
baseline data exists) run as a NEW dispatch with the extractor inference-attempt
ceiling raised 300s -> 1200s FOR THIS RUN ONLY. No code, config, History, or
production changes. No timeout value is proposed from this sample (one data
point either way is not calibration).

- Run stamp (UTC): `20260917T110113`. Isolated out dir:
  `/tmp/calib-1200s-20260917T110113/` (measurements, progress log, calibration
  report, harness stderr/stdout as transport evidence, `run_meta.json`).
- Persistent evidence (sanitized):
  `logs/calibration-1200s-20260917T110113/` (same 6 files, no secrets, no
  document text — verified by keyword scan).
- Historical artifacts preserved untouched: all 7 prior `logs/calibration-*/`
  dirs and `/tmp` archives from the 300s-ceiling runs.
- `api/.env` byte-identical before/after (`sha256 7942bfbdc924bf87…`,
  verified in-process before dispatch and after completion).
- Override vehicle: `/tmp/opencode/diag_1200_run.py` (outside the repo; nothing
  added to git). In-process patch of `calibrate_timeouts` `EXPERIMENT_*`
  constants (300/550/600/3600 intact on disk, see §6); inline process env for
  `Settings` (never written to `api/.env`); `attempt_ceiling_s` ContextVar to
  the transport boundary. Precedent for the patch pattern:
  `api/tests/test_timeout_calibration_progress.py` monkeypatches the same
  constants per-test.

## 1. Outcome: timed-out-at-1200s

Single record: outcome `partial`,
`validation_errors: ["Extractor failed: LLM request timed out"]`,
`judge_status: unavailable`, `n_fields 0`, accuracy `tp0/fp0/fn3` recorded
without interpretation. Timeout source is the inference-attempt transport
timeout (extractor 1200.11s vs 1200s ceiling, attempt 1 of 1,
`timeout_retries=0`) — not the 1400s doc deadline (doc 1316.70s) and not the
1500s outer bound (wall 1319.9s, harness exit 0).

| Stage | Transport (per-HTTP) | Stage-block | Status |
|---|---|---|---|
| ocr | — | 3.53s | measured |
| router | 111.62s ok | 111.64s | measured (reproduces prior 101.73s) |
| extractor | 1200.11s censored | 1200.30s | measured, first-inference-timeout → doc stopped |
| judge | — | 0.0s | not_run (provably never ran) |

Record carries `effective_request_s: 1200.0` (record + dispatch detail), so
this run is distinguishable from the seven 300s-ceiling runs. New datum: this
doc's extractor prompt is 8107 chars (`sha 131aa93c4175`) — same size class as
the promptsize batch (7985–9035 chars), consistent with prompt overhead
dominating over document text.

## 2. Slow-vs-hung verdict: favors hang/bug; proof still short

8/8 extractor attempts censored at exactly their ceiling (7 x 300s + 1 x 1200s),
zero completions. Quadrupling the ceiling bought no completion and no partial
progress signal (timeouts record no usage/tokens; outcome invariant). The
router completes reproducibly in ~100–112s on the same doc/model/host while the
extractor never completes at 4x the ceiling — stage asymmetry persists. That
points AWAY from "just needs more time" and TOWARD a hang/bug (provider-side
stall on the extractor's larger output contract under CPU inference, or an
interaction defect — not determined by this run).

Residual ambiguity (explicit): one 1200s sample cannot prove an infinite hang
(completion at >1200s is unfalsified), and server-side state remains unknown
(client timeout never proves server cancellation; `server_cancellation`
honestly reads "unknown …" in the record). The question is therefore resolved
directionally but not deductively. NO production timeout value follows from
this sample in either direction.

## 3. Bounds compliance (all met, real observations)

| Bound | Observed | Status |
|---|---|---|
| ≤1200s per inference attempt | router ok 111.62s; extractor timeout 1200.11s (hit ceiling = first inference timeout, doc stopped) | met |
| ≤1400s document processing | doc `total_seconds` 1316.70 | met |
| ≤1500s overall incl. preflight + cleanup | wrapper wall 1319.9s, harness exit 0 | met |
| ≤2 inference HTTP sends | `guard_sends 2, guard_blocked 0` (router + extractor; judge never attempted — natural stop, guard untriggered) | met |
| No retries / fallbacks / corrections / confirm / apply | `timeout_retries 0`; conditions `retries 0, fallbacks 0, corrections 0`; `confirm []`; `applied {false, "not requested"}` | met |
| Stop after first inference timeout | extractor attempt 1 timeout → `partial`, judge `unavailable`, no further dispatch | met |
| Stop before dispatch if idleness unprovable | pre-dispatch gate all-true: `/api/ps` empty, `ollama ps` empty, no `llama-server` worker, port :8000 free, History `queued/processing` empty, tags show `qwen2.5:3b` (no pull) | idleness established, dispatched |

Stage limit was 1250s (not 1200s) by design: headroom so a ~1200s attempt is
attributed to the transport timeout rather than racing the stage guard. The
1200s bound applied to the inference attempt itself (`effective_request_s`).
All three stages shared the harness's single `EXPERIMENT_STAGE_S` constant
(router/judge observed ~112s/never-run, so no practical effect).

## 4. Persistence outcome: PASS

`progress.jsonl` has 2 lines: `doc_start` persisted BEFORE inference, then
`doc_outcome`. Full `calibration_report.json` written on the exit-0 path
(`task_elapsed_s` 1316.72, `store_write_errors []`). Exactly 1 record,
`total_dispatches 2 / uncertain 0`, no auto-resume or second run, no harness
process lingering (`/api/ps` empty again post-run), nothing applied.

## 5. Measurement-fidelity outcome: PASS

- Document start saved before inference: yes (line 1 of `progress.jsonl`).
- Dispatch events survive nested collectors: yes — `dispatches 2`,
  `dispatches_known 2`, `dispatch_intents 2`, `terminal_by_outcome {ok: 1,
  timeout: 1}`, `dispatches_uncertain 0`.
- Successful and timed-out attempts retain measured durations: yes —
  `attempt_durations.router [111.62]` (ok) and
  `censored_durations.extractor [1200.11]` (never merged; extractor p95
  withheld, `n_censored 1`).
- Stage durations from the actual emitted contract: yes — top-level `timings`:
  ocr 3.53 / router 111.64 / extractor 1200.30, all `measured`; judge `0.0` /
  `not_run` (provably never ran after the extractor failure).
- Unknown values null, never fabricated zeros: yes — extractor/judge summary
  quantiles null with `p95_withheld: true` and explicit reasons.
- Timeout observations censored: yes — extractor p95 withheld; router p95
  111.62 from n=1 is `provisional_tail: true`, candidate 139.52 reported but
  NOT adopted (`confirm []`, `applied false`).
- Counts/durations agree with independent transport evidence:
  `dispatches 2 / timeouts 1 / errors 0` agrees with harness stderr
  (`stage=extractor ... attempt=1 duration=1200.1s category=timeout
  timeout_retries=0` + the matching `LLM request timed out ... timeout=1200s`
  line); router block 111.64 vs transport 111.62 and extractor block 1200.30
  vs censored 1200.11 are coherent magnitudes; total 1316.70 ≈
  3.53+111.64+1200.30 + ~1.2s overhead. Nothing guessed.
- Post-run `/api/ps` empty (model unloaded after expiry) — idle again, not a
  discrepancy. History `completed 8, failed 1` unchanged (live DB read-only;
  result cache off; temp storage only).

Note: the report's `production_timeouts` object (1200/1250s) reflects the
in-process experimental values read from `Settings` at runtime, NOT
`api/.env` — the env file is byte-identical (see §6). Do not quote those
numbers as production settings.

## 6. Temporary-override revert: CONFIRMED (all four layers)

1. Harness file untouched: `api/scripts/calibrate_timeouts.py` mtime predates
   the run; constants on disk still 300.0 / 550.0 / 600.0 / 3600.0
   (`git diff` empty for the file; untracked status is pre-existing).
2. `api/.env` untouched: sha256 `7942bfbdc924bf87…` before dispatch and after
   completion (in-process check + post-run shell check agree).
3. Process env override auto-reverted: inline `LLM_REQUEST_TIMEOUT_SECONDS=1200
   ...` assignment existed only for the single shell command's process tree,
   which has exited (no lingering process).
4. No repo additions: override vehicle lives at `/tmp/opencode/diag_1200_run.py`
   (outside git); `git status` shows no new tracked files from this run
   (only the pre-existing branch diff and untracked `logs/` dirs, now plus
   `logs/calibration-1200s-20260917T110113/`).

## 7. Checks executed (real output)

- `ruff check api/` → **All checks passed**.
- Focused (7 files): `test_prompt_size` + `test_failed_stage_calibration` +
  `test_timeout_calibration_progress` + `test_timeout_calibration` +
  `test_dispatch_observability` + `test_http_budget` +
  `test_stage_failure_timing` → **76 passed** (50.99s).
- Full backend `python -m pytest api/tests/ -q` → **715 passed, 2 skipped**
  (111.32s; skips pre-existing; matches the promptsize-batch baseline exactly).
- Frontend build: skipped — no UI changed (per `/ecc-verify` rule).
- Diagnostic: `... timeout -s TERM 1500s bash -c 'source .venv/bin/activate &&
  exec python /tmp/opencode/diag_1200_run.py --out
  /tmp/calib-1200s-20260917T110113'` → harness exit 0, wall 1319.9s.

## 8. Rollback (this task only)

Nothing to roll back in the repo: no code, config, or History changes. To
reproduce: re-run the §7 diagnostic command with a fresh `--out` dir (requires
fresh authorization — exactly-one-document scope is spent). To clean up: `rm
-rf /tmp/calib-1200s-20260917T110113 /tmp/opencode/diag_1200_run.py
logs/calibration-1200s-20260917T110113` (historical 300s-ceiling artifacts must
be left intact).

## 9. Workflows consulted vs checks actually run

- Consulted: project-local `/ecc-verify` (§7 checks), `/ecc-update-docs`
  (this report), `AGENTS.md` rules, prior reports
  `extractor_timeout_investigation_2026-09-17.md`,
  `promptsize_calibration_batch_2026-09-17.md`,
  `calibration_ledgerfix_smoke_X51008142033_2026-09-17.md` (context only —
  claims re-verified, not assumed).
- Actually run: `ruff`, focused + full `pytest` with real output above,
  read-only pre/post probes (`/api/tags`, `/api/ps`, `ollama ps`, `pgrep`,
  port, History), one bounded `--only` diagnostic under `timeout 1500s`, file
  read-backs above, sanitized persistence under `logs/`. NOT run (out of scope
  / constrained): frontend build, second run, `--confirm`/`--apply`,
  production-setting changes, downloads, History changes, pushes.
