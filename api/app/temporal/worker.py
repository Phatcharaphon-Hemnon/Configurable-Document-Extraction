"""Standalone Temporal worker process.

Run from the ``api/`` directory:

    python -m app.temporal.worker

Requires a running Temporal server (e.g. ``temporal server start-dev``).
"""

from __future__ import annotations

import asyncio

from temporalio.worker import Worker

from app.temporal.activities import (
    classify_activity,
    extract_activity,
    judge_activity,
    parse_activity,
    validate_activity,
)
from app.temporal.client import TASK_QUEUE, get_temporal_client
from app.temporal.workflows import ExtractDocumentWorkflow


async def main() -> None:
    client = await get_temporal_client()

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[ExtractDocumentWorkflow],
        activities=[
            parse_activity,
            classify_activity,
            extract_activity,
            validate_activity,
            judge_activity,
        ],
    )
    print(f"Temporal worker listening on task queue: {TASK_QUEUE}")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
