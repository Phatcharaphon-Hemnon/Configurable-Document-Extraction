# Region budget boundary enforcement — 2026-09-16

## Defect

The region-entry check (`page_dispatches + 4 > cap`) was insufficient: a
generation started with one remaining page attempt could still dispatch up to
four requests through transport retries, tier fallbacks, and corrective
generation, overshooting the page/job ceilings by up to three dispatches.

## Fix (no live inference; mocked transport + temp storage only)

Remaining page/job budget is now enforced at EVERY outbound HTTP boundary:

- `DispatchBudget` (`api/app/services/request_control.py`): task-local
  remaining page/job attempts, set by the region loop around each region call
  (remaining = cap − used), unset elsewhere (default-off and non-region paths
  unchanged). `claim_dispatch_slot()` debits exactly one attempt per dispatch;
  denied attempts debit nothing; exhaustion is never retried, never slept.
- `api/app/services/client.py`: gate before each transport attempt in
  `_chat_with_retry`, plus explicit pre-checks before tier fallback and the
  single corrective generation (text and vision paths). Denials surface an
  explicit `budget-exhausted` `ClientError` — never a tier fallback, never a
  generic provider error.
- `api/app/services/extraction_service.py`: scopes the budget per region call;
  on exhaustion mid-page, completed regions are preserved (validated partials),
  the failed region carries explicit `budget-exhausted` status, later regions
  resolve as `unresolved` (never silently skipped, never retried past the cap),
  the page is excluded from the completed-result cache, and a mechanical
  `page:region-budget` finding forces review. Diagnostics carry
  `region_request_version` + `region_compact_request`.
- Fingerprints: result cache gains `provider.region_compact_request` and
  `versions.region_request`, so contract bumps invalidate deterministically
  (checkpoints already embed the version via the model fingerprint).

No timeout raises, no output-capacity cuts, no model/provider switches, no
weakened validation, no deadline changes.

## Tests

New `api/tests/test_region_budget_boundary.py` (RED-first, all green):

- one remaining attempt + retryable 429 → exactly 1 request, `ClientError`;
- one remaining attempt + explicit tier rejection → exactly 1 request (no fallback);
- one remaining attempt + unparseable output → exactly 1 request (no correction);
- zero remaining → zero requests; job cap binds like the page cap;
- exactly-once debit (24→23 / 64→63 on one success);
- exhaustion never sleeps/retries (no HTTP, no backoff);
- service level: mid-page exhaustion preserves the completed region's
  evidenced field, sets `needs_review`, publishes explicit budget status,
  `complete_for_cache is False`.

Extended `api/tests/test_region_requests.py`: 6-combo flag matrix (region path
× region contracts × extraction compact), semantic/evidence coverage (every
kept field's span ⊆ its region text), checkpoint + result-cache invalidation
on version bump.

Fixed in passing: brittle hand-enumerated settings namespace (now real
`Settings` with tmp overrides), dead unused import, two new unused imports.

## Results

- `ruff check api/`: clean (baseline had 1 pre-existing dead-import error).
- Full backend `python -m pytest api/tests/ -q`: 654 passed, 2 skipped
  (pre-existing skips), no live inference.
- Previously failing `test_fingerprints_differ_by_region_request_flag`,
  `test_region_model_fingerprint_covers_flag_and_version`, and
  `test_service_opt_in_sends_kind_contracts` now pass.

## Residual

The region-entry worst-case check is intentionally kept alongside the boundary
gate (advisory pre-flight + per-request enforcement). No latency claim is made
from offline size measurements; `summarize_region_requests` stays content-free.
