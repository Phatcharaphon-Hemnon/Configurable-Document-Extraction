# LLM request queue and retries

The default deployment is one API process using one Ollama account, with
`TEMPORAL_ENABLED=false`. Ollama Free allows one concurrent request; the
application defaults `LLM_MAX_CONCURRENT_REQUESTS=1` (positive integer).
This limiter is independent of the incoming HTTP upload rate guard.

## Job scheduling

The background `run_job` entry point acquires a FIFO asyncio lock before OCR
or stage timers begin. Jobs remain `queued` until admitted, then become
`processing`. Pages run sequentially and retain their original order. Uploads
still return 202 immediately, and identical pending uploads reuse their job.
The existing upload guard bounds the number of accepted pending jobs per client.

Both job stores support `processing`. SQLite uses an existing text column, so
no database migration is required. Successful/failed result handling retains
its existing contracts, including completed results that require review.
Cancellation marks waiting or active jobs failed and releases the lock. On
restart, both queued and processing rows are marked failed: upload bytes are
not persisted for durable resumption.

## Provider calls

`services/request_control.py` shares a semaphore across all Client instances
for an endpoint in the running event loop. Each public generation holds its
slot through backoff and output-format fallbacks. A task-local Pydantic
`RequestBudget` in `schemas/llm_control.py` counts at most four HTTP attempts
across the entire generation; OpenAI SDK retries remain disabled.

- Transient 429/503: retry identical arguments after 3/6/12 seconds plus up to
  0.5 seconds jitter. A valid numeric or HTTP-date Retry-After is a minimum delay.
- Timeout: at most one same-request retry after two seconds, within the same
  four-attempt budget. Exhaustion stops the generation, never changes format.
- Permanent authentication/billing/quota errors fail immediately.
- Only a 400/422 explicitly rejecting reasoning/extra_body allows removing that
  parameter. Only explicit response-format rejection or invalid returned JSON
  allows the next output format. These attempts also consume the shared budget.
- Stage deadlines remain authoritative. Retry waits that cannot fit fail
  immediately; cancellation during a request or sleep releases the provider slot.

Job and stage ContextVars add identifiers to attempt/backoff logs. No request
body or document text is added to those logs. A shared process limiter cannot
coordinate other processes or apps using the same account. Temporal and
multi-worker deployments need separate account-wide admission coordination.

## Frontend

Uploads show Uploading, Queued, Processing, then their terminal result.
Polling runs every five seconds without a ten-minute queue cutoff. Each status
fetch has a 30-second network timeout. Disposing the hook aborts its polling.
Network errors retain the accepted job ID and offer Reconnect, which polls the
existing job without resubmitting the upload. A genuine failed job can be
resubmitted using Retry Upload. Page reload recovery remains available in History;
there is no new browser persistence or background polling after disposal.

## Verification

Run `.venv/bin/python -m pytest api/tests/test_request_queue.py` from the root.
Existing client timeout, fallback, rate-limit, and async-job tests cover related
contracts. From `web`, run `npm test` for polling and hook orchestration tests,
and `npm run build` for TypeScript and production bundling.

For a live smoke test after restart, submit four different uploads including
a multipage PDF. Expect one processing job, the others queued, ordered page
results, and no overlapping provider attempts. Provider capacity outside this
application can still cause throttling; the bounded retry policy reports it.
