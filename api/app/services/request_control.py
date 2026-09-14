"""Task-local request budgets and process-local provider concurrency control."""

import asyncio
import logging
import time
from contextvars import ContextVar
from functools import wraps
from weakref import WeakKeyDictionary

from app.schemas.llm_control import RequestBudget

logger = logging.getLogger(__name__)

request_budget: ContextVar[RequestBudget | None] = ContextVar("request_budget", default=None)
job_context: ContextVar[str] = ContextVar("llm_job", default="-")
stage_context: ContextVar[str] = ContextVar("llm_stage", default="-")
stage_deadline: ContextVar[float | None] = ContextVar("stage_deadline", default=None)
# Each event loop owns its locks; clients for the same endpoint share one.
_limiters: WeakKeyDictionary = WeakKeyDictionary()


def limited_generation(method):
    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        if request_budget.get() is not None:
            return await method(self, *args, **kwargs)
        loop = asyncio.get_running_loop()
        settings = getattr(self, "settings", None)
        endpoint = getattr(settings, "llm_base_url", "default")
        if not isinstance(endpoint, str):
            endpoint = "default"
        limit = getattr(settings, "llm_max_concurrent_requests", 1)
        if not isinstance(limit, int) or limit < 1:
            limit = 1
        limiters = _limiters.setdefault(loop, {})
        semaphore = limiters.setdefault(endpoint.rstrip("/"), asyncio.Semaphore(limit))
        # Provider queue wait is measured OUTSIDE the HTTP attempt timing so
        # timeout attribution can separate "waited for a slot" from "provider
        # was slow". Content/credentials never enter logs — endpoint only.
        _queued_at = time.perf_counter()
        async with semaphore:
            _queue_wait = time.perf_counter() - _queued_at
            logger.info(
                "LLM provider queue wait endpoint=%s wait=%.2fs",
                endpoint, _queue_wait,
            )
            token = request_budget.set(RequestBudget())
            try:
                return await method(self, *args, **kwargs)
            finally:
                request_budget.reset(token)
    return wrapped
