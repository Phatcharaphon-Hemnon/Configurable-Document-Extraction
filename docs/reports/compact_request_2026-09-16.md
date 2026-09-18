# Compact extraction requests — implementation report (2026-09-16)

Branch: `perf/latency-opt-20260914`. Opt-in candidate optimization targeting
the measured bottleneck below. Production defaults unchanged
(`EXTRACTION_COMPACT_REQUEST=false`).

## 1. Attributed evidence (server-log correlated, scoped to one request)

Diagnostic streaming request for `sroie_X51008142033.jpg`, job
`334f03b7-de2e-49d7-9c18-79ea243926cc`
(ollama-local / `qwen2.5:3b`, `http://localhost:11434/v1`):

| Measurement | Value | Source |
|---|---|---|
| Prompt tokens (observed, server-counted) | **2084** | ollama server log `task.n_tokens = 2084` |
| Prompt evaluation | **~74s** (≈28 tok/s CPU) | server `print_timing`: 2048 tok @ 73.54s |
| Model load before task | ~4–5s (cold: `/api/ps` empty pre-run) | `load_model` 04:11:48 → slot work 04:11:53 |
| Prompt KV-cache reuse | 0 (`cached n_tokens = 0`) | server log |
| Generation (server-side) | ~8.7→8.1 tok/s, 564 tokens, `truncated = 0`, then client-cancelled | server log 04:13:07–04:14:19 |
| Client outcome | 0 chunks, overall 150s deadline → `TimeoutError` | `/tmp/diag_extractor_stream.json` |
| Server request lifetime | 2m29s (`POST /v1/chat/completions` 200) | GIN log 04:14:19 |
| Inference device | CPU (`size_vram = 0`), `n_slots = 1` | diag report `model_state_after` |

Attribution: **server-side compute, not transport**. The SDK `create` call
never returned (`established_at` unknown) — per the task rules this alone
proves nothing, but the server log shows continuous prompt processing for
the whole window, so no transport stall is inferred or needed. Prompt
evaluation alone (~74s) exceeds the 45s production request timeout, so a
production non-streaming request cannot survive prompt eval on this
page/model/host, let alone generation. Scope: this request only — a cold
model load preceded it, and per-request timings vary; identical timing for
warm requests is NOT claimed. Observed 2084 tokens are server-counted;
character-based estimates below are marked as estimates.

Client/server consistency note: the server generated 564 tokens while the
client assembled 0 chunks — the client's 150s overall deadline fired at
~149–150s, just before the nearly-complete server response, and the
disconnect cancelled the server task (`stop: cancel task`). Closing the
client never stops server computation; it only stops waiting.

## 2. Exact serialized request audit (offline rebuild, no inference)

Rebuilt with current code + stored OCR text (682 chars, 17 invoice catalog
fields). All sizes in characters (estimates, not tokens):

| Section | Chars | Share |
|---|---|---|
| Intro (`Extract data…`) | 61 | 1% |
| Compact catalog (17 fields) | 813 | 11% |
| `_COMMON_RULES` | 1813 | 24% |
| Document text section | 731 | 10% |
| `_build_prompt` subtotal | 3461 | 47% |
| JSON suffix: raw schema dump (`Schema:` + full schema JSON) | 3349 | 45% |
| JSON suffix: output-contract paragraph (table-inside-tables rule) | 600 | 8% |
| Wire prompt total (`prompt + suffix`) | **7410** | 100% |
| PLUS `response_format` wire schema (same schema, 2nd serialization) | 3445 | — |

Finding: the schema (~3.4k chars) is serialized **twice** on the
`json_schema` tier — once as prompt text
(`client.py: _generate_structured_inner` appends `_build_json_prompt_suffix`
unconditionally), once structurally via `response_format` (`_call_with_schema`
sends `_pydantic_to_json_schema`). Region requests carry the same double
payload through the shared builder (`extract_call` → `_run_call` →
`generate_structured`) on top of the full catalog + full rules per region.

## 3. Candidate optimization (reversible, opt-in)

`EXTRACTION_COMPACT_REQUEST` (default `false`; production behavior
byte-identical when off — verified: full suffix 3949 chars before and after;
full backend suite green).

When `true`, on the `json_schema` tier only: the prompt-text schema dump is
omitted; a short pointer plus the proven output-contract paragraph is kept
(797 chars vs 3949, −3152). `json_object` sends only
`{"type": "json_object"}` (no schema) and `plain` sends no `response_format`
at all, so both tiers always keep the full suffix — the prompt text is their
only schema contract. Tier fallback to `json_object`/`plain` rebuilds
messages with the full suffix; the corrective generation references "the
enforced response schema" only on `json_schema`, otherwise "the Schema
above". (Review correction 2026-09-16: the initial draft also compacted
`json_object`; that tier carries no structural schema, so the dump was
retained there. Code, comments, and `test_compact_request.py` now enforce
json_schema-only compaction.) The wire
`response_format` always carries the full schema; catalog, rules, source
text, evidence requirements, and the typed result (`ExtractionResponseSchema`
→ `ExtractionCallResult`) are untouched — deterministic conversion is the
identity, so page reconciliation, coverage tracking, rejected candidates,
row-local evidence, repeated columns, zeros, and Judge inputs are preserved
by construction. Region calls reuse the shared builder, so region requests
benefit automatically with no region-code change.

Fingerprinting (old results cannot masquerade as new): `compact_request` is
fingerprinted in the result-cache provider config AND the region
model-fingerprint (region checkpoints + manifest keys invalidate across
modes). `PROMPT_VERSION` is unchanged because default-mode prompts are
unchanged.

Before/after for the failing page (chars, estimate): wire prompt
**7410 → 4258 (−43%)**; at the measured density (7410 chars → 2084 tokens ≈
3.56 chars/token) ≈ 2084 → ~1200 prompt tokens, i.e. prompt-eval work ≈
74s → ~43s at the observed 28 tok/s.

## 4. Region-contracts decision (audited, deferred by design)

Region requests still carry the full catalog + full wire schema after this
change. Per-kind wire-schema/catalog narrowing was NOT implemented: the
splitter assigns table bands that can contain the only printed instance of a
scalar (narrowing the catalog risks silent field loss), and merge,
reconciliation, validator, and Judge all consume the full typed contract
(narrowing the wire schema risks unsupported mappings). The preserve-list
outranks the conditional scoping clause. Narrowing remains a follow-up
candidate gated by the experiment below.

## 5. Remaining limits (explicit)

- Mock parity proves request construction only — **smaller requests do not
  establish measured speed improvement**.
- Even at ~1200 tokens, prompt eval (~43s est.) sits near the 45s deadline
  before any generation; CPU inference (`size_vram = 0`), single slot, and
  `max_tokens = 3000` generation at ~8 tok/s dominate end-to-end latency.
- Quality parity is unverified (no live inference was run): compact mode
  must still demonstrate equal extraction quality, not just equal parsing.
- Savings apply only while the `json_schema` tier is negotiated (the
  default strongest tier). Providers/models that explicitly reject
  `json_schema` fall back to `json_object`/`plain`, which keep the full
  suffix by design — no reduction there, also by design. The A/B commands
  below record the negotiated tier per run; compare tiers before comparing
  timings.

## 6. Next experiment (PREPARED ONLY — do not execute yet)

Bounded original-vs-compact comparison on the same page/model/host:

- Command A (original): authorized run of
  `python api/scripts/diag_extractor_single.py --job-id
  334f03b7-de2e-49d7-9c18-79ea243926cc --out /tmp/diag_compact_A.json` with
  `EXTRACTION_COMPACT_REQUEST=false`.
- Command B (compact): same with `EXTRACTION_COMPACT_REQUEST=true` →
  `/tmp/diag_compact_B.json`.
- Comparable conditions (recorded, not assumed): `ollama /api/ps` equal
  (warm model loaded in both), no concurrent jobs (`LLM_MAX_CONCURRENT_REQUESTS=1`,
  one API process), same catalog overlay hash, same stored OCR text hash.
- Dispatch caps: single-attempt, no retries/fallbacks/corrective (script
  default); deadlines: request 45s / stage 150s / overall 180s.
- Correctness checks: both outputs parse to `ExtractionResponseSchema`;
  field/table counts, names, values, `source_span` verbatim-ness, and
  validator acceptance compared field-by-field (quality bar: compact output
  identical or review-equivalent — compact must not lose rows, evidence, or
  zeros; SROIE-scored fields alone are NOT the bar).
- Stop conditions: stop after A+B complete; do NOT raise deadlines, cut
  `EXTRACTION_MAX_TOKENS`, switch models, restart services, or touch
  History on failure — record and report.

## 7. Files changed / tests / rollback

- `api/app/core/config.py` — `EXTRACTION_COMPACT_REQUEST` (default false).
- `api/app/services/client.py` — `_EXTRACTION_OUTPUT_CONTRACT` (extracted,
  default text byte-identical), `_build_compact_suffix`,
  `compact_request_enabled` (Mock-safe `is True`),
  `build_structured_messages` (shared builder), `_corrective_instruction`,
  tier-aware wiring in structured + legacy vision paths with plain-fallback
  restore.
- `api/app/services/result_cache.py`, `api/app/services/extraction_service.py`
  — `compact_request` fingerprinted (page cache + region checkpoints).
- `api/scripts/diag_extractor_{single,stream}.py` — shared builder +
  recorded `compact_request`/`message_chars` (stream script only).
- `api/.env.example` — documented opt-in (commented; effective defaults
  unchanged).
- `api/tests/test_compact_request.py` — 9 regressions (default parity,
  json_schema dump-removal + contract-retention, json_object/plain restore,
  json_schema→json_object fallback restore, wire schema intact, corrective
  wording per tier, deterministic replay, fingerprint split).
- Verify: `ruff check api/` clean; full backend suite **609 passed,
  2 skipped** (order-isolation fix: unique test endpoint + capability-memory
  reset fixture; +2 regressions from the json_schema-only review correction).
- Rollback: revert the files above and delete
  `api/tests/test_compact_request.py`; this document remains as the record.
