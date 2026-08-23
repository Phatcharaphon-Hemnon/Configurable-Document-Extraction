"""Standalone Temporal worker process for the document-extraction PoC.

Run from the ``Backend/`` directory:

    python -m app.temporal.worker

Requires a running Temporal server (e.g. ``temporal server start-dev``).
"""

from __future__ import annotations

import asyncio

from temporalio.worker import Worker

from app.temporal.client import TASK_QUEUE, get_temporal_client
from app.temporal.activities import classify_document
from app.temporal.workflows import ClassifyDocumentWorkflow


async def main() -> None:
    """Connect to Temporal and run the worker until interrupted."""
    client = await get_temporal_client()

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[ClassifyDocumentWorkflow],
        activities=[classify_document],
    )
    print(f"Temporal worker listening on task queue: {TASK_QUEUE}")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
