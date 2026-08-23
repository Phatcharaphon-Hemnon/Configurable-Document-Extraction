"""Temporal client helper for the document-extraction PoC."""

from __future__ import annotations

from temporalio.client import Client

# Task queue constant shared by the worker, workflow starter, and route
# handler.  Imported by ``worker.py`` and ``routes.py`` to avoid magic
# string duplication.
TASK_QUEUE = "poc-extraction-queue"


async def get_temporal_client() -> Client:
    """Return a fresh Temporal client connected to ``localhost:7233``.

    No connection pooling or caching for this PoC — each call opens a new
    gRPC channel.  A production deployment would use a singleton managed
    by the FastAPI lifespan.
    """
    return await Client.connect("localhost:7233")
