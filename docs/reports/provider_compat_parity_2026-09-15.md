# Provider compatibility + branch parity: implementation report (2026-09-15)

No pushes made. No live provider calls. `api/.env`, History (15+1 jobs),
catalogs, and local models untouched throughout.

## 1. Starting / ending commits

| Tree | Start | End |
|---|---|---|
| perf (`perf/latency-opt-20260914`, main checkout) | `f7a48f9` + uncommitted work | `5603e48` via `aca6e83` (checkpoint) → `b543f71` (compat+Groq) → `3de1203` (log hygiene) → `8bea0b8`+`f9ecc9b` (replay) → `5603e48` (parity doc) |
| wt-main (`main`) | `f7a48f9` | `5eb688a` (4 cherry-picks `-x` + lint + parity doc) |
| wt-chore (`chore/eval-3b-combined-report`) | `3a7f1a2` | `318f856` (`07e868d` fast path first, then the same 4 picks + lint + parity doc) |

Pre-existing divergence was 5 main-only / 4 chore-only, neither ancestor.
The two NVIDIA provider commits are byte-identical content (parallel
commits). Divergent histories preserved — cherry-picks only, no merges,
rebases, force-pushes, or wholesale copies.

## 2. Refactored modules (why)

- `api/app/services/provider_capabilities.py` (new): typed
  `CapabilityProfile` + `resolve_capabilities()` (exact endpoint/model →
  provider default → unknown-conservative) + one `build_chat_kwargs()`
  used by every text/image agent path. One maintained rule set replaces
  three hand-rolled kwargs blocks in `client.py`.
- `client.py`: kwargs construction delegated to the builder; reasoning
  levels enforced per exact pair (known-unsupported values clamped, never
  sent); capability memory bounded (512 entries, 24h TTL,
  `reset_capability_memory()`); `ClientResult.output_mode` records the
  tier actually used; capability source in start logs.
- `config.py`: native `groq` row (`GROQ_API_KEY`, `openai/gpt-oss-20b` @
  0.6 — documented 2026-09-14, not live-verified).
- `result_cache.py`: `compat_policy` fingerprint version (old entries
  invalidate on policy change).
- Structure improved by separating *capability knowledge* (profiles table)
  from *mechanism* (builder, budget, recovery) — see
  `docs/reference/provider_compatibility.md` for the marked matrix
  (documented / tested / unsupported / unknown).

## 3. Deletion inventory (evidence for each)

- **Deleted/untracked**: nothing from version control. `api/logs/` added
  to `.gitignore` (restart logs proved the tree noise; file kept on disk).
- **Retained with reason**: all 60+ app modules referenced/tested/entry
  points (`main.py`, `temporal/worker.py`, operator scripts in
  `api/scripts/` per `docs/guides/api_scripts.md`); `rag_retriever.py`
  (used by `ingest_kb.py`, tested, documented); tracked
  `logs/security_audit.jsonl` (stale snapshot but a documented example
  path); `eval_artifacts/*.json`, `eval_report.md` (dated, labeled
  historical evidence); `.ua/` knowledge graph; `skills/`; `data/`
  runtime + `data/regression_refs/` fixtures; web entry/types only
  unreferenced (everything else imported). Tier-fallback blocks in
  `generate_structured*` left duplicated deliberately (behavioral risk
  outweighs dedup benefit; builder already unified kwargs).

## 4. Intentional differences / unresolved divergence

Final `git diff --name-only main chore` = `README.md` +
`api/.env.example` (deployment values only: cloud/key vs local/keyless).
No unresolved behavioral divergence. Cloud/local distinction preserved
(endpoints, auth, defaults, keyless local startup verified).

## 5. Checks run (isolated storage; real History/catalogs untouched)

- `ruff check api/`: clean in all three trees.
- Backend `pytest`: **503 passed, 2 skipped** in wt-main and wt-chore
  (perf: 502 + replay fix + parity-doc = same set; rerun post-edit below).
- New: `test_provider_compat.py` (13: profiles, builder, native/legacy
  Groq parity, memory bounds/expiry/reset, output_mode, fingerprint
  version), `test_replay_parity.py` (fixtures + normalized dump).
- Replay: `REPLAY_DUMP` outputs **byte-identical** across perf/main/chore
  (accepted fields + verbatim evidence, 1 table / 8 cells, completeness
  1.0, no review flags).
- Web: 10/10 + build ok in both worktrees (node_modules symlinked from
  main checkout, removed after). Playwright browsers absent →
  browser-driven UI checks **blocked** (unit + build green instead).
- Config: keyless `ollama-local` resolves (empty key, loopback, temp 0.0);
  cloud defaults intact. Secret scan of staged diffs: clean (only
  `nvapi-...` doc placeholders).
- One real find fixed: `test_cache_generation_params` was env-fragile
  (developer `api/.env` leaked `DISABLE_STRICT_JSON_SCHEMA=true` into its
  base; proven via stash-bisect on the clean checkpoint + env-override
  rerun) — now pins all fingerprinted settings via monkeypatch (hermetic).
- Blocked: all live provider probes, free-model default confirmation,
  browser runs, end-to-end latency/accuracy claims.

## 6. Cache migration / rollback

Fingerprint gained `compat_policy: compat-v1` → pre-existing cached
results miss once and recompute (no migration script needed; TTL/eviction
unchanged; incomplete/unavailable-Judge outcomes still uncached).
Rollback per group (reverse order, no pushes): revert parity-doc commit →
revert replay commits → revert log-ignore → revert compat commit →
revert checkpoint (restores `f7a48f9` tree). Worktrees removable with
`git worktree remove --force` after review; remotes untouched.

## 7. Review summary (push approval requested, not executed)

perf holds 6 focused commits (`aca6e83`, `b543f71`, `3de1203`,
`8bea0b8`, `f9ecc9b`, `5603e48`); wt-main and wt-chore hold the
equivalent cherry-picked stacks on their own ancestry. `git status`
clean everywhere except ignored runtime files (`data/`, `api/logs/`).
To publish: `git push origin main` from wt-main and
`git push origin chore/eval-3b-combined-report` from wt-chore —
normal pushes, remotes currently stale and fast-forwardable.
