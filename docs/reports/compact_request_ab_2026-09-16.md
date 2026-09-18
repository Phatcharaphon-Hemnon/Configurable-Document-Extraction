# Compact-request A/B pilot — measured results (2026-09-16)

Bounded original-vs-compact comparison on `sroie_X51008142033.jpg`
(job `334f03b7-de2e-49d7-9c18-79ea243926cc`), authorized max 2 local
inference HTTP attempts. Both conditions ran; both timed out. No code
changed; production defaults untouched (`EXTRACTION_COMPACT_REQUEST`
still unset → `false`).

## 1. Harness + finite deadlines (stated before running)

Prepared harness: `api/scripts/diag_extractor_single.py` (non-streaming,
single-dispatch Extractor LLM-path diagnostic). Exact finite deadlines:

| Deadline | Value | Source |
|---|---|---|
| Per-request (transport) timeout | **45s** | effective `LLM_REQUEST_TIMEOUT_SECONDS=45` (`api/.env`), recorded in both reports as `request_timeout_s: 45.0` |
| Outer stage deadline | **150s** | `STAGE_DEADLINE_S = 150.0` (`diag_extractor_single.py:54`) |
| Overall run deadline | **180s** | `OVERALL_DEADLINE_S = 180.0` (`diag_extractor_single.py:55`) |

Single-attempt enforcement (process-local, verified in code
`diag_extractor_single.py:129-172`): client budget patched to 1 attempt /
0 timeout retries, dispatch-counting guard aborts any 2nd HTTP dispatch,
no Router, no Judge, no tier fallback, no corrective generation. Stored
OCR text used (provenance-gated); temp catalog copy; nothing written to
result cache, History DB, or source store.

Parity across A/B by construction: same endpoint/model, same stored OCR
text (682 chars, sha `2b72d4cd…`), same temp-copied live catalog, same
`ExtractionResponseSchema`, same output budget (`EXTRACTION_MAX_TOKENS`
effective **3000**, code default 8000), same non-streaming mode, same
deadlines. Only `EXTRACTION_COMPACT_REQUEST` differed, applied as a
process-local env override (never written to `api/.env`).

## 2. Serving implementation + effective settings

- Ollama: PID **512**, `/usr/local/bin/ollama serve`, started 03:34:35;
  version 0.33.3; `n_slots = 1`, CPU (`size_vram = 0`), ctx 4096.
- API (uvicorn, PID 7265, `--reload`, since 03:44:10) was idle: no
  queued/processing jobs before either attempt (read-only check).
- File mtimes (`client.py`/`config.py` 04:40) post-date both process
  starts — verification limit: mtimes suggest reload, not proof of loaded
  code. The diag ran in a fresh interpreter against current files; the
  comparison is A-vs-B in the same interpreter state, so staleness (if
  any) affects both equally.
- Effective (`.env`, secrets excluded): provider `ollama-local`,
  endpoint `http://localhost:11434/v1`, model `qwen2.5:3b`,
  `EXTRACTION_MAX_TOKENS=3000`, request/stage timeouts 45/150s,
  `REGION_EXTRACTION_ENABLED` unset → `false`. Temperature numeric value
  is **unknown** in these reports (single-dispatch report does not record
  it); identical by construction (same `Settings()` code path).

## 3. Provenance (read-only, `data-local/extraction.db` only)

`data/extraction.db` was never opened (listed only to confirm two files
exist; canonical path per `DATABASE_PATH` is `data-local/`). Stored page
payload: filename `sroie_X51008142033.jpg`, size 184361 B matches source
original (`cfbd3f60-…`); text 682 chars / 109 blocks / engine
`tesseract`; `failed_stage: extractor`, `failed_error: LLM request timed
out`. Catalog `invoice_fields.json`: 17 fields,
sha256 `5f881bcc…`.

## 4. Pre-attempt states

- **Pre-A:** `/api/ps` → `{"models":[]}` (cold, idle); no active jobs;
  `MemAvailable 2689492 kB`, `SwapFree 2714540 kB`; server log idle since
  04:14:19; prompt cache enabled (limit 8192 MiB), cache state 0 prompts.
- **Pre-B:** server idle confirmed (`all slots are idle` 04:49:10, no new
  task; `/api/ps` model loaded but no task evidence); no active jobs;
  `MemAvailable ~973–1020 MB`. Model **warm** (loaded, expiry extended to
  04:53:59) — confound vs A's cold start (see §7).

## 5. Commands executed (exact)

```bash
EXTRACTION_COMPACT_REQUEST=false python api/scripts/diag_extractor_single.py --job-id 334f03b7-de2e-49d7-9c18-79ea243926cc --out /tmp/diag_compact_A.json
EXTRACTION_COMPACT_REQUEST=true  python api/scripts/diag_extractor_single.py --job-id 334f03b7-de2e-49d7-9c18-79ea243926cc --out /tmp/diag_compact_B.json
```

Two transport dispatches total (one per condition). No retries,
fallbacks, corrective calls, Router, Judge, or warm-up inference.
`json_schema` tier accepted by the endpoint in both — no rejection path
taken.

## 6. Measured outcomes

| | A (`compact=false`) | B (`compact=true`) |
|---|---|---|
| Client status | `failed-one-dispatch` | `failed-one-dispatch` |
| Client error / chain | `ClientError: LLM request timed out` / TimeoutError×2, CancelledError | identical |
| Client duration | 45.042s | 45.067s |
| Negotiated tier / purpose / outcome | `json_schema` / initial / timeout | `json_schema` / initial / timeout |
| Offline wire message chars | 7382 | 4230 (−3152, −43%) |
| Server `task.n_tokens` | **2090** | **1322** (−768, −37%) |
| Server `cached n_tokens` at task start | 0 (cold) | **1142** (prefix reuse) |
| Server progress in-window | prompt eval 512 tok @16s, 1024 @33s; cancelled mid-eval (release `n_tokens=1536`) | prompt eval skipped via cache → generation `n_gen=338` @~8.8–9.2 tok/s; cancelled mid-generation (release `n_tokens=1665`) |
| Server HTTP status | 500 @45.0s (cancelled) | 500 @45.0s (cancelled) |
| Completion tokens (server-reported usage) | **unknown** (no completed response) | **unknown** (partial 338 observed server-side, no client-usable output) |
| Fields/tables accepted | 0 (no completed call) | 0 (no completed call) |
| Prompt-eval / first-content / completion times | prompt eval incomplete — no first-content (non-streaming); completion n/a | prompt eval ~0 new work (cache hit); first-content time **unknown** (non-streaming client exposes no incremental timestamps); completion n/a |

Deterministic validation / evidence / rejected-candidate comparison: not
applicable — neither condition produced a completed call, so there are no
accepted/rejected fields or tables to compare. No output parity exists;
no speedup is calculated (B's 45s window is the same timeout cutoff, and
per task rules a timeout cutoff is not a speed measurement).

## 7. Confounds (recorded, not hidden)

1. **Order/cold-vs-warm:** A ran cold (4.5s model load inside its window,
   zero cache); B ran warm (model resident). Warmth favours B.
2. **Prompt KV-cache reuse:** B's `cached n_tokens = 1142` means only
   ~180 of its 1322 tokens needed fresh eval (shared template/catalog
   prefix with A's prompt despite different suffixes). B's progress into
   generation is therefore NOT attributable to compaction alone.
3. **Memory pressure:** `MemAvailable` 2669 MB → 1004 MB across A (model
   load), 974 MB → 854 MB across B; swap declined correspondingly.
4. **Pilot size:** two attempts = pilot, not a benchmark; per-request
   timings vary; no general performance claim follows.

## 8. Attribution + limitations

- Both failures attribute to the **45s request deadline firing during
  server-side compute** (exception chain + 45.0s durations + server
  cancel/release logs), not transport: A died in prompt eval, B died in
  generation. Missing usage tokens = "no completed response/usage
  recorded" (never inferred from CPU or logs).
- Compact mode measurably shrinks the request (chars −43%, server prompt
  tokens −37%) and, combined with cache/warmth, B reached generation
  within the same deadline — but B still did not complete, quality parity
  is unverified (no parseable output in either condition), and the cache
  confound blocks any compaction-only speed claim.
- Follow-up candidates (not run): warm-cache-controlled A/B, higher
  request deadline as a measurement instrument only, streaming
  first-content timing, region-path comparison. None authorized here.

## 9. Files changed / rollback / workflows

- Files changed: **none** (this report only). `EXTRACTION_COMPACT_REQUEST`
  remains unset (default `false`); no production settings, History (6
  completed / 1 failed before and after), services, models, or pushes
  touched. Isolated outputs: `/tmp/diag_compact_A.json`,
  `/tmp/diag_compact_B.json`. Rollback: n/a.
- Workflows consulted: project `/ecc-debug` rules (evidence-first,
  effective-vs-default settings, no secrets/gold/inference-beyond-scope)
  and `/ecc-update-docs` target/format. Tools actually run: `read`/`bash`
  evidence gathering (provenance queries, `/api/ps`, `journalctl`,
  meminfo), the two authorized diag dispatches, one offline
  no-inference size rebuild. No tests re-run (suite state documented in
  `compact_request_2026-09-16.md`: 609 passed, 2 skipped).
