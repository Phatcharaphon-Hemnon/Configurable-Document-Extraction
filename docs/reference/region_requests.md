# Region requests (opt-in, default OFF)

Two independent opt-in flags shape the region path; both default OFF and the
full-page path is byte-identical when OFF:

| Flag | Env | Effect when `true` |
|---|---|---|
| Region path | `REGION_EXTRACTION_ENABLED` | Split pages along OCR geometry into focused region tasks (header / table / totals / notes / page fallback). |
| Region contracts | `REGION_COMPACT_REQUEST` | Prepend a kind-specific output contract to each region task (role context + evidence rules). Requires the region path. |

`EXTRACTION_COMPACT_REQUEST` (extraction-level schema-dump removal) never
alters region task text; the flag matrix is covered in
`api/tests/test_region_requests.py::test_flag_matrix_region_path_and_contracts`.

## Scoped contracts (what they are and are not)

- Prompt-level guidance only, reusing the shared extractor prompt downstream.
- The wire schema stays the full `ExtractionResponseSchema` — contracts NEVER
  restrict extraction to a subset of fields (in particular never to SROIE's
  scored fields); full catalog names remain allowed in every region.
- Region bodies, row boundaries, and context notes travel verbatim; no
  geometry is added or altered (`test_request_text_adds_no_geometry`).
- Disabled mode returns the legacy builder output byte-identically
  (`test_disabled_mode_is_byte_identical_to_legacy`).
- Typed conversion into the existing `ExtractionCallResult` contract preserves
  evidence, repeated columns, printed zeros, and conflicts, and rejects
  cross-page calls (`convert_region_call`).
- Merge keeps first-occurrence fields; identical (name, value, span) triples
  collapse with a dedup note; differing values become conflicts retained for
  review. Table data rows are never deduped.

## Coverage preservation

The splitter's coverage ledger is untouched: every source block stays
assigned / excluded (blank only) / unresolved. Unresolved blocks and
ambiguous boundaries flag the page incomplete via reconciliation; totals
arithmetic yields review findings only — printed amounts are never rewritten.

## Dispatch budgets (finite, sequential, per-boundary)

One region call costs up to the shared 4-attempt generation budget
(`RATE_LIMIT_MAX_RETRIES`; transport retries, tier fallback, and the single
corrective generation share it). Page/job ceilings are unchanged:

- `MAX_REGION_DISPATCHES_PER_PAGE = 24`, `MAX_REGION_DISPATCHES_PER_JOB = 64`
  (`api/app/services/regions.py`).
- Region-entry check (`page_dispatches + 4 > cap`) stops new regions before
  exceeding ceilings; remaining regions resolve as `unresolved` with an
  explicit detail (never silently skipped).
- Per-HTTP-boundary enforcement (`DispatchBudget` in
  `api/app/services/request_control.py`, gated in `api/app/services/client.py`):
  the region loop scopes remaining page/job attempts around each region call;
  every outbound attempt claims one slot and is debited exactly once; once any
  cap is reached, transport retries, tier fallbacks, and corrective
  generations are blocked WITHOUT sending another request, surfacing an
  explicit `budget-exhausted` `ClientError`. Completed regions are preserved
  (validated partials), later regions are marked `unresolved`, the page is
  excluded from the completed-result cache, and a mechanical
  `page:region-budget` finding forces review.
- Output capacity and deadlines are never altered; no timeout, model, or
  provider changes ride along with budgets.

Regression coverage: `api/tests/test_region_budget_boundary.py` (one remaining
attempt + retryable error / format rejection / tier fallback → exactly one
request; zero remaining → zero requests; exactly-once debit; exhaustion never
retried; service-level partial preservation) and `test_http_budget.py`
(shared 4-attempt cap at the transport boundary).

## Fingerprints and invalidation

- `REGION_REQUEST_VERSION` (`region-request-v1`) enters region checkpoint
  fingerprints (via the model fingerprint) and the completed-result cache
  (`versions.region_request` + `provider.region_compact_request`).
- Any contract/builder/conversion change bumps the version so old checkpoints
  recompute and old cache entries resolve as misses instead of masquerading
  as new-mode results.

## Offline size reporting (no latency claim)

`summarize_region_requests` reports per-region serialized sizes and
duplicated-context chars only. Smaller individual requests do NOT imply faster
runs — the report carries no timing claim of any kind.
