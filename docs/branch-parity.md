# Branch parity report — shared logic, two deployments

Date: 2026-09-14. No force-push, no branch deletion, no reset, no credential
copying. Runtime DB/sources/caches/credentials were never copied between
installations.

## 1. Branch-to-deployment mapping (stated before editing)

- `main` = **cloud/API-key deployment**. Intended template:
  `LLM_PROVIDER=ollama-cloud`, `LLM_MODEL=gpt-oss:20b`, `LLM_API_KEY` empty in
  the tracked template (real key goes into the ignored `api/.env`).
- `chore/eval-3b-combined-report` = **local Ollama deployment**. Intended
  template: `LLM_PROVIDER=ollama-local`, `LLM_MODEL=qwen2.5:3b` (verified from
  the local ignored `api/.env` + `ollama list`, which shows `qwen2.5:3b` and
  `qwen2.5:1.5b`; the effective local `.env` selects `qwen2.5:3b`). No cloud
  key required (`ollama-local` ignores `LLM_API_KEY`).

Both branches keep OCR on CPU (`OCR_ENGINE=tesseract` default) and provider
concurrency at one (`LLM_MAX_CONCURRENT_REQUESTS=1`).

## 2. Before / after commits

Before (verified after `git fetch --all --prune`):

- `main` = `0bbdfe72ad1254f3fe0e7484612a44a8d6a5f01b`
- `chore/eval-3b-combined-report` = `e5b193348cf7d5951639cfd1e5db3e058a005ed6`
- `chore` was 8 commits behind `main`, 0 ahead; `chore` is an ancestor of
  `main` (`git merge-base --is-ancestor`). No unique commits on `chore`.
- Both tracked `api/.env.example` files selected `ollama-cloud` /
  `gpt-oss:20b` (confirmed via `git show <ref>:api/.env.example`).
- Working tree on `main` held extensive uncommitted logic work plus staged
  untracking deletions; all of it was preserved (nothing reset). One
  accidentally deleted tracked asset
  (`api/app/data/knowledge_base/ground_truth/ICR.png`, still referenced by
  `manifest.json`) was restored with `git restore`; the staged untracking of
  the runtime DB (`data/extraction.db`, now ignored, file kept on disk) and
  of `.ua/.trash-*` / `.ua/intermediate` / `eval_artifacts/metrics.partial.json`
  was kept as intended.

After (pre-report-file state; the two `docs/branch-parity.md` commits on top
are byte-identical content on both branches):

- Shared baseline: `716b0f4` — uncommitted working-tree logic committed on
  `main` (evidence/acceptance, result cache, hybrid OCR, classified recovery,
  UI terminal stage, OCR consolidation, catalog/thai updates, tests, docs).
- Shared predictability: `edcef74` — `GET /` exposes effective
  `llm_provider`/`llm_model`/`llm_base_url`/`ocr_engine`/
  `llm_max_concurrent_requests` (no credentials); `scripts/run_all.sh` never
  overwrites an existing `.env`, prints intended-template vs effective-`.env`
  provider/model without secrets, warns on mismatch, and only warns about an
  empty key when the effective provider requires one.
- `main` = `bf0b9db` (shared + cloud deployment config).
- `chore/eval-3b-combined-report` = `1b95f7d` (shared + local deployment
  config, fast-forwarded through `edcef74` with `git merge --ff-only`).
- Merge-base of the two heads = `edcef74` (all shared logic).
- Nothing has been pushed; remote refs are unchanged
  (`origin/main` = `0bbdfe7`, `origin/chore/eval-3b-combined-report` =
  `e5b1933`). Review the local commits, then push each branch normally (no
  force).

## 3. Confirmed logic differences and reconciliation

Committed `chore..main` before this task held 8 main-only commits (extractors,
judge, validator, security/coherence gate, catalogs, extraction service,
field catalog, local OCR, eval scripts, evidence/catalog tests, review docs,
eval artifacts) — all newer application logic, none of it deployment config.
`chore` had zero unique commits, so no merge was needed: `chore` was
fast-forwarded to the shared baseline (`716b0f4` + `edcef74`).

Synchronized (identical) logic areas: OCR preparation/recovery/page geometry
(`load_page_images`, Tesseract primary → coherence-gated RapidOCR fallback,
hybrid opt-in, `OCRBlock` geometry); router/extractor prompts; Pydantic
schemas/serialization; evidence/type/table/role validation (page-local
evidence, no elsewhere-fallback); catalog contents + registration rules;
judge inputs/skip conditions/review status; local + Temporal orchestration;
queueing/retry budgets/error classification; persistence/caching/source
references/History; frontend rendering/progress/exports; regression tests.

## 4. Allowed remaining differences (allowlist)

`git diff --name-only main chore/eval-3b-combined-report` returns exactly:

- `api/.env.example` — provider/model + header only (`ollama-cloud` /
  `gpt-oss:20b` vs `ollama-local` / `qwen2.5:3b`; keys empty in both tracked
  templates; all other variables identical, including OCR CPU + concurrency 1).
- `README.md` — deployment-mode badge + backend `api/.env` setup section only
  (cloud key instructions vs local no-key + `ollama pull qwen2.5:3b`).

Every other tree entry is byte-identical. Capability differences (e.g.
structured-output tiers) are handled in the shared provider adapter
(`Client._strongest_tier` + provider-confirmed unsupported-tier memory +
  shared 4-attempt budget) — there is no per-branch extraction logic.

## 5. Exact tests executed and results

Same code, both branches (repos at `bf0b9db` / `1b95f7d`):

- `ruff check api/` — clean on both.
- `python -m pytest api/tests/ -q` — **412 passed, 2 skipped** on both
  (working-tree baseline was also 412/2 before branching).
- `cd web && npm test` (`node --test tests/jobQueue.test.mjs
  tests/pipelineStage.test.mjs`) — **9 passed** on both.
- `cd web && npm run build` (`tsc -p tsconfig.json && tsc -p
  tsconfig.node.json && vite build`) — success on both.
- Deterministic parity harness (no network): `ollama-local`/`qwen2.5:3b` vs
  `ollama-cloud`/`gpt-oss:20b` settings resolve to `http://localhost:11434/v1`
  (temp 0.0) vs `https://ollama.com/v1` (temp 1.0); both start at the
  `json_schema` tier via the shared adapter; retry budget `(4, 1)`; empty keys
  accepted at config time; `accept_page` on a fixed invoice fixture is
  deterministic and **identical on both branches
  (fixture sha `29e6e2993d91`, 1 accepted / 0 rejected / coverage 1.0)**.
  Only legitimate per-run differences (IDs, timestamps, source URLs, provider
  metadata) were normalized; model-generated answers were never required to
  match across models.
- Isolated storage: `SQLiteJobStore` create/list/stats against a temp
  `DATABASE_PATH`/`SOURCE_STORAGE_PATH` — OK (runtime `data/` untouched).
- Setup dry-run: `run_all.sh` bash syntax OK; on `main` with the local
  developer `.env` it correctly reports intended=`ollama-cloud` vs
  effective=`ollama-local` (mismatch); on `chore` it reports
  intended=`ollama-local` vs effective=`ollama-local` (match). Existing
  `.env` files are never overwritten.
- No identical-code claims were used as bug-fix proof; the audit below is from
  actual working-tree reads with `file:line` evidence.

## 6. Live checks performed or blocked

- Local Ollama inventory: `ollama list` shows `qwen2.5:3b` (1.9 GB) and
  `qwen2.5:1.5b`; effective `api/.env` selects `qwen2.5:3b`. No live local
  generation was required for parity (harness is deterministic and offline).
- Live cloud checks: **blocked — no cloud credentials available**
  (`LLM_API_KEY` and `OLLAMA_API_KEY` both absent; presence booleans only,
  values never printed). Cloud endpoint/model/key handling was verified at the
  config/adapter level only. Run a live cloud extraction after setting the key
  in `api/.env` on the `main` checkout.

## 7. Inherited unresolved bugs (reported separately, not relabeled)

Audit of the current tree (not history) found the three suspected issues
**absent** — identical code was not taken as proof; each was checked:

- Whole-page table-evidence fallbacks: **not present**. No
  `source_span = page_text/full_text` synthesis exists; validator
  (`api/app/agents/validator.py:165-166`) and acceptance
  (`api/app/services/acceptance.py:339-350,449-464`) reject cells/fields whose
  own span does not resolve page-locally; `check_evidence`
  (`api/app/core/security.py:230-247`) requires the span in-document plus
  value-in-span. `evidence.py:94-107` resolves the model's quote against page
  text (returns `[]` on no match) and never substitutes page text as evidence.
- Mutable per-call state: **no leaking state**. Module-level mutables are all
  legitimate bounded process caches (`Client._shared_clients`,
  `Client._unsupported_tiers`, OCR/catalog caches, single-flight job maps with
  completion cleanup) or constants; per-request data is task-local
  (`ContextVar`) or page-isolated (`deepcopy`, local `seen`).
- Excessive JSON regeneration: **not present**. `generate_structured`
  performs one initial + at most one corrective generation with a shared
  4-HTTP-attempt budget (`RATE_LIMIT_MAX_RETRIES=4`, `TIMEOUT_MAX_RETRIES=1`,
  `max_retries=0`); confirmed truncation raises honestly; tier downgrade
  happens only on explicit provider rejection, never on schema failure.
- Historical evaluation reports (`eval_report.md`, `eval_artifacts/`) were left
  untouched by the deployment commits and remain labeled with the
  model/config that produced them; no local measurements were relabeled as
  cloud results.

## 8. Safe setup, switching, synchronization, rollback

- Fresh setup per branch: `git checkout <branch>` → `cp api/.env.example
  api/.env` (only if missing; `run_all.sh` does this and never overwrites) →
  `main`: paste the cloud key into `api/.env`; `chore`: `ollama pull
  qwen2.5:3b` + `ollama serve` (no key) → `./scripts/run_all.sh` → verify
  with `GET /` (`llm_provider`/`llm_model`, no secrets) or
  `python -c "from app.core.config import Settings; ..."`.
- Switching branches does **not** change the ignored `api/.env`; always
  re-check `run_all.sh`'s intended-vs-effective lines after a checkout.
- Simultaneous local + cloud: prefer separate worktrees/checkouts
  (`git worktree add ../<dir> <branch>`) so `.env` files, `data/` History DBs,
  `data/sources/`, and `.cache/` never collide. Never copy runtime DBs,
  sources, caches, or credentials between installations.
- Explicit env overrides (`LLM_BASE_URL`, `ROUTER/EXTRACTION/JUDGE_MODEL_NAME`,
  `LLM_TEMPERATURE`, `DISABLE_STRICT_JSON_SCHEMA`) are preserved by `Settings`
  and untouched by the deployment commits.
- Future synchronization: keep all logic changes on shared commits applied
  identically to both branches (or on `main` then fast-forward `chore` while
  it remains an ancestor); reserve branch commits for `api/.env.example` +
  `README.md` deployment values only. Re-verify with
  `git diff --name-only main chore/eval-3b-combined-report` (must list only the
  two allowlisted files) plus the test/parity commands in §5.
- Rollback: deployment commits are leaf-only (`bf0b9db` on `main`, `1b95f7d`
  on `chore`, plus these identical report commits); revert the leaf commit on
  the affected branch only. Shared commits (`716b0f4`, `edcef74`) revert
  identically on both branches if ever needed. No pushes have been made, so
  no remote rollback is required.

## 9. Resync 2026-09-15 (compat refactor + fast-path port + replay parity)

Worktrees built from each branch's own head (`/tmp/opencode/wt-main` @
`f7a48f9`, `/tmp/opencode/wt-chore` @ `3a7f1a2`); divergence was 5 main-only
/ 4 chore-only commits, neither side an ancestor. Ported by cherry-pick
(`-x`, no merges, no rebases, no force):

- `chore` first received main's `07e868d` (fast path — shared core logic it
  lacked), then both branches received the identical shared stack:
  latency/streaming/judge/reasoning/xkiro checkpoint, provider-compat
  profiles + Groq row, log-ignore hygiene, replay harness + lint fix.
- The NVIDIA provider commits (`f7a48f9` vs `3a7f1a2`) are byte-identical
  content (parallel commits); no conflict source.
- Deployment values preserved per side through every pick: `chore` kept
  `ollama-local`/`qwen2.5:3b`, `main` kept `ollama-cloud`/`gpt-oss:20b`
  (verified by diff after each pick).
- Final `git diff --name-only main chore` lists exactly `README.md` +
  `api/.env.example` (deployment values only) — the parity invariant holds.

Verification (isolated storage; History/catalogs untouched):
`ruff` clean + backend **503 passed, 2 skipped** in both worktrees; web
10/10 + build ok in both (node_modules symlinked from the main checkout,
then removed; Playwright browsers absent → browser-driven UI checks
blocked, unit + build green). Deterministic replay
(`api/tests/test_replay_parity.py` + fixtures, `REPLAY_DUMP`): normalized
outputs **byte-identical across perf/main/chore trees** (accepted fields +
evidence, 1 table / 8 cells, completeness 1.0, no review). Local keyless
startup resolves (`ollama-local`, empty key, loopback URL); cloud defaults
intact. Live provider/browser checks remain blocked (no live calls
authorized). No pushes made — see the implementation report for the review
summary.
