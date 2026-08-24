"""Temporal client helper."""

from __future__ import annotations

from temporalio.client import Client

from app.core.config import get_settings

TASK_QUEUE = get_settings().temporal_task_queue


async def get_temporal_client() -> Client:
    """Connect to the Temporal frontend at TEMPORAL_ADDRESS (default localhost:7233)."""
    return await Client.connect(get_settings().temporal_address)
