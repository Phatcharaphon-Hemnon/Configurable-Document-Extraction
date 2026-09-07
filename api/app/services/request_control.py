"""Task-local request budgets and process-local provider concurrency control."""

import asyncio
from contextvars import ContextVar
from functools import wraps
from weakref import WeakKeyDictionary

from app.schemas.llm_control import RequestBudget

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
        async with semaphore:
            token = request_budget.set(RequestBudget())
            try:
                return await method(self, *args, **kwargs)
            finally:
                request_budget.reset(token)
    return wrapped
