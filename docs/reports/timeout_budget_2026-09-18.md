# Timeout budget: slow-local profile (request 480s) — 2026-09-18

## Symptom
API startup printed each timeout warning twice (6 lines): 3 from
`validate_timeout_config` logging directly plus 3 re-logged by the service
as `"Startup timeout config: ..."`.

- `LLM_REQUEST_TIMEOUT_SECONDS=480` against equal 480s Router/Extractor/Judge
  limits leaves no room for a timeout retry (`2x480+2 ~= 962s` needed).

## Analysis
- `TimeoutGuard.track` (`api/app/guards/timeout_guard.py`) cancels a stage at
  its limit, so with request == stage the first LLM attempt alone can consume
  the window: the same-tier timeout retry (`api/app/services/client.py`,
  `TIMEOUT_MAX_RETRIES=1`, 2s backoff) and rate-limit backoffs can never run.
- The extractor additionally hosts the single corrective generation inside the
  same stage window (`extraction_service.py`). The timeout-retry budget is
  shared across both calls (at most 1 total), so the honest extractor floor is
  `3x request + backoffs` (initial + retry + corrective).
- The 480s request itself is justified: the local 3B model took 177s for a
  truncated 3000-token output; full 8000-token generations take longer.
  Cutting the request timeout would kill healthy generations — stages must be
  sized above it, not equal to it.
- Verified: timeout values are NOT part of the result-cache fingerprint
  (`result_cache.py::config_fingerprint_dict`), correctly so — they change
  timing, never output content. No cache invalidation on this change.

## Fix (model unchanged)
| Setting | Before | After | Floor math |
|---|---|---|---|
| `LLM_REQUEST_TIMEOUT_SECONDS` | 480 | 480 (kept) | — |
| `ROUTER_TIMEOUT_SECONDS` | 480 | 1000 | `2x480+2 = 962` |
| `JUDGE_TIMEOUT_SECONDS` | 480 | 1000 | `2x480+2 = 962` |
| `EXTRACTOR_TIMEOUT_SECONDS` | 480 | 1500 | `3x480+4 = 1444` |

- `api/app/core/config.py::validate_timeout_config`: pure function again —
  returns strings, never logs (the service log line is the single emission
  point, ending the double print). Warnings are actionable: each names the
  computed floor and the suggested `*_TIMEOUT_SECONDS>=N` value; the extractor
  message names the `initial + retry + corrective` composition.
- `api/.env` + `api/.env.example`: values above plus refreshed comments
  (the 45s-design comment now points here for the slow-local profile).
- Tests in `api/tests/test_timeout_config.py`: purity/single-emission,
  480-equal warns with `>=1000/>=1500` suggestions, 480-fixed-values silent,
  extractor floor composition case.

## Accepted cost
Worst case a page can occupy the worker up to ~25 min (1500s extractor cap)
before failing, blocking the queue at `LLM_MAX_CONCURRENT_REQUESTS=1`. That
is the price of retry coverage on a slow local model; the alternative is
fail-fast with zero recovery.

## Verification
- `ruff check api/` clean.
- `python -m pytest api/tests/test_timeout_config.py -q` (7 tests).
- Full `python -m pytest api/tests/ -q`.
- Restart API from `api/`: zero timeout warnings on startup.
