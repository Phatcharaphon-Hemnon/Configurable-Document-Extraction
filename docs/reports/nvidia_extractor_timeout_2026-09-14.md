# NVIDIA Extractor timeout: diagnosis + bounded fix (2026-09-14)

Branch: `perf/latency-opt-20260914`, HEAD `f7a48f9` (+ uncommitted, see §7).
Effective (secret-safe, values only — no key displayed):
provider `nvidia`, endpoint `https://integrate.api.nvidia.com/v1`,
model `openai/gpt-oss-20b`, `LLM_REQUEST_TIMEOUT_SECONDS=45`,
`ROUTER/JUDGE=100`, `EXTRACTOR=150`, `EXTRACTION_MAX_TOKENS=3000`,
`LLM_TEMPERATURE=0.0`, strict `json_schema` tier first, concurrency 1.

## 1. Source verification (reproduction, not comparison)

Job `cdda27ff-f883-4fc7-b917-d42546dc3157` stores filename **Invoice1.jpg**
(`image/jpeg`, 373178 bytes); source `985d4625-…/original` is present and
**sha256-identical to the ground-truth fixture** (`69f0ac05…`). It is not
Delivery1.webp — no inference from timeout logs was used.

The stored payload confirms this job IS the failing original (not a later
success): document-level `failed_stage=extractor`,
`error="Extractor failed: LLM request timed out"`, `fields=[]`,
`judge_status=unavailable`, timings `router=9.93s / extractor=92.02s /
pipeline=102.14s`, usage `router {803 tokens, attempts=1}`,
`extractor {attempts=2, retries=1, NO token counts}` — i.e. 45s + 2s backoff
+ 45s with zero bytes received, while Router on the same endpoint/model
succeeded. Repeated request timeouts; not invalid JSON (nothing arrived),
not a provider outage (Router worked). Job-level `error_details`:
`TimeoutError`, no status/code/request_id (transport timeout, not a
provider error body).

## 2. Diagnostic design and budget (all limits kept)

Isolated scripts (`/tmp/opencode/diag_invoice1.py`, `diag_repeat.py`;
tracked code untouched by the harness): verified Invoice1.jpg bytes,
**stored `full_text` (709 chars) reused** — measures the LLM/extraction
path, not upload-to-result. Same endpoint/model/validation rules; tmp KB
copy (no catalog writes), tmp storage, result cache off, explicit
`doc_type=invoice` (the job's own routed type — saves 1 router attempt per
call). 120s HTTP timeout, **timeout retry disabled in-process only**
(`TIMEOUT_MAX_RETRIES=0`; rate-limit backoff code retained), finite
150s stage guard per call. Every `_chat_with_retry` entry counted
(incl. correctives). Production defaults untouched.

| Call | Shape | Wall | HTTP attempts | Outcome |
|---|---|---|---|---|
| 1 control | current shape | 79.7s | 2 (initial + corrective) | extractor FAILED: **empty output, `finish_reason='length'`, tokens 2155/3000/5155**, fields=0 |
| 2 low | + `reasoning_effort="low"` (extractor schema only) | 41.7s total (extractor **13.99s**) | 3 (extractor 1 + judge 2) | extractor OK: **10 fields + 1 table**, score 0.75, `needs_review=True` (legitimate semantic findings); judge returned empty → `unavailable` |
| 3 control repeat | current shape | 144.1s | 2 (initial + corrective) | extractor FAILED: **identical signature** — empty, `length`, **2155/3000/5155** |

Budget: **7/8 HTTP attempts**, ~5 min wall of ~10 min. No billing changes.
`reasoning_effort="low"` was **accepted** (no 400, no fallback-removal log);
that confirms support for this exact pair — it does not by itself prove it
suppresses reasoning, but the 14s/878-token completion vs two 3000-token
empty completions is the measured effect.

## 3. Reading the evidence (exploratory — see §4 for strength)

- The control failure is NOT "slow generation": with 120s available the
  model filled all 3000 completion tokens yet returned **zero visible
  content** with `finish_reason='length'`. The output budget was consumed by
  non-visible (reasoning) tokens — consistent with default medium reasoning
  effort plus the undocumented `extra_body.reasoning` flag being silently
  ignored by the hosted gateway (no 400 ever observed, so the strip-and-retry
  path never fired). The 45s production timeouts are the same stall cut off
  earlier.
- The repeat reproduces the signature **exactly, including identical token
  counts (2155/3000/5155)** — deterministic model-side behavior, not timing
  noise. Elapsed differs (79.5s vs 143.5s: provider speed variance), outcome
  does not.
- The single low-effort call contrasts sharply (14s, full coverage) but with
  n=1 on that side, provider load/output variability cannot be excluded.

## 4. Conclusion strength (honest grading)

**Suspected contributor: unbounded reasoning volume on gpt-oss-20b at
default (medium) effort interacting with `max_tokens=3000`.** Supporting
evidence: two identical empty/`length`/3000-token control failures vs one
fast full-coverage low-effort success, plus docs confirming the parameter
exists and defaults to medium. **Not a proven cause**: the low side has one
sample, and load variance is real (control elapsed varied 79→144s). No
causal claim from the A/B pair alone; no production default changed on this
evidence.

## 5. Reference-annotation check (identical ≠ correct)

Provisional manifest reference for Invoice1 vs call-2 accepted output:
matches — `invoice_date` 13/01/2018, `seller_phone`, `tax_id`,
`subtotal/tax/total/paid/change` amounts; table extracted (1 of 2 reference
tables). Discrepancies (reported, not hidden) — `invoice_number` accepted
as `S00012726` vs reference `CS00012726` (faithful to OCR text per its
source_span; possible OCR drop of "C"), `seller_name`/`seller_address`
correctly held back by the semantic role-evidence rule (`needs_review`
findings are legitimate), discount/rounding/cashier/time/total_quantity and
the second table absent. Score 0.75 with review=True is the honest outcome;
parity call1-vs-call2 is N/A (call1 produced nothing).

## 6. Fix delivered (default behavior unchanged)

| File | Change |
|---|---|
| `api/app/services/client.py` | `generate_structured(..., reasoning_effort=None)`; documented top-level `reasoning_effort` replaces undocumented `extra_body` when active (never both); exact-pair `REASONING_EFFORT_ALLOWLIST` (now contains the verified `integrate.api.nvidia.com` + `openai/gpt-oss-20b` pair); **separate** rejection path — explicit unsupported-parameter response removes only the param within the same budget, never downgrades the output tier, remembers the rejection; effective setting on `ClientResult` + `LLM reasoning effective` log |
| `api/app/core/config.py` | `LLM_REASONING_EFFORT` env (default `""` = current behavior, byte-identical; only low/medium/high accepted) |
| `api/app/services/result_cache.py` | `reasoning_effort` in provider fingerprint (old entries/manifests miss on change) |
| `api/app/services/request_control.py` | `LLM provider queue wait` log separates slot-wait from HTTP attempt time |
| `api/.env.example` | Documents the knob (commented; default untouched) |
| `api/tests/test_reasoning_effort.py` (new, 7 tests) | Default byte-identical, explicit replaces extra_body, exact-allowlist gating (incl. empty-list proof), rejection-without-tier-downgrade + memory + effective recording, config validation, fingerprint invalidation, queue-wait log |

Deliberately unchanged: timeouts, token limits, temperature, tiers, budgets,
backoff, concurrency, provider/model. Raising timeouts was not used as a
fix and is not claimed to improve speed.

## 7. Verification, scope, rollback, blocked checks

- `ruff check api/` clean; backend **451 passed, 2 skipped** (7 new incl.);
  web 10/10 + build ok. This branch also still carries the earlier
  router-timeout work (config/client/web changes) — preserved, not re-tested
  here beyond the suite.
- Rollback: unset `LLM_REASONING_EFFORT` (default already `""`) and/or revert
  the client/config/cache/request_control/test files; the allowlist entry is
  inert without the env var. `force_refresh`/`disable_caches` semantics kept.
- **Blocked / unmeasured**: end-to-end upload-to-result improvement (budget
  spent on the LLM path; reusing stored OCR text by design) — needs a
  full-path run in a follow-up budget; live verification of the consolidated
  Judge prompt (judge returned empty in call 2, a separate observation not
  chased here); browser flow against a live backend.
- Follow-up (new evidence required): repeat low-effort extraction to
  strengthen/weaken §4; only then consider defaulting the override with
  latency + full-coverage quality gates.
