"""Timeout guards for pipeline stages."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeVar

from app.services.request_control import stage_context, stage_deadline

logger = logging.getLogger(__name__)
page_timings: ContextVar[dict | None] = ContextVar("page_timings", default=None)
stage_notifier: ContextVar[Callable | None] = ContextVar("stage_notifier", default=None)

T = TypeVar("T")


@dataclass
class StageTimeoutConfig:
    """Timeout configuration for each pipeline stage.

    Defaults mirror production: each limit sits above the worst single LLM
    call (request timeout + one same-tier retry), so healthy and recovering
    calls pass while fully stalled stages fail fast.

    All values are in seconds.
    """

    router_seconds: float = 100.0
    extractor_seconds: float = 150.0
    validator_seconds: float = 10.0
    judge_seconds: float = 100.0
    ocr_seconds: float = 120.0
    vision_seconds: float = 90.0


DEFAULT_TIMEOUT_CONFIG = StageTimeoutConfig()


class StageTimeoutError(Exception):
    """Raised when a pipeline stage exceeds its timeout."""

    def __init__(self, stage: str, timeout: float):
        self.stage = stage
        self.timeout = timeout
        super().__init__(f"Stage '{stage}' timed out after {timeout:.1f}s")


@asynccontextmanager
async def stage_timeout(
    stage: str,
    timeout: float,
):
    """Context manager that enforces a timeout on async operations.

    Usage:
        async with stage_timeout("router", 30.0):
            result = await router.classify(...)

    Args:
        stage: Name of the pipeline stage (for error messages).
        timeout: Maximum time in seconds.

    Raises:
        StageTimeoutError: If the operation exceeds the timeout.
    """
    try:
        yield
    except asyncio.TimeoutError:
        raise StageTimeoutError(stage, timeout)


async def run_with_timeout(
    coro: Callable[..., Awaitable[T]],
    stage: str,
    timeout: float,
    *args: Any,
    **kwargs: Any,
) -> T:
    """Run a coroutine with timeout enforcement.

    Args:
        coro: Async callable to run.
        stage: Name of the pipeline stage (for error messages).
        timeout: Maximum time in seconds.
        *args: Positional arguments for the callable.
        **kwargs: Keyword arguments for the callable.

    Returns:
        Result of the coroutine.

    Raises:
        StageTimeoutError: If the operation exceeds the timeout.
    """
    try:
        return await asyncio.wait_for(coro(*args, **kwargs), timeout=timeout)
    except asyncio.TimeoutError:
        raise StageTimeoutError(stage, timeout)


class TimeoutGuard:
    """Timeout guard that tracks stage execution times.

    Usage:
        guard = TimeoutGuard(StageTimeoutConfig(router_seconds=30.0))

        async with guard.track("router"):
            result = await router.classify(...)

        # Access timing data
        print(guard.get_timings())
    """

    def __init__(self, config: StageTimeoutConfig | None = None):
        self.config = config or DEFAULT_TIMEOUT_CONFIG
        self._timings: dict[str, list[float]] = {}

    def _get_timeout(self, stage: str) -> float:
        """Get timeout for a stage."""
        timeout_map = {
            "router": self.config.router_seconds,
            "extractor": self.config.extractor_seconds,
            "validator": self.config.validator_seconds,
            "judge": self.config.judge_seconds,
            "ocr": self.config.ocr_seconds,
            "vision": self.config.vision_seconds,
        }
        return timeout_map.get(stage, 60.0)

    def _record_timing(self, stage: str, duration: float) -> None:
        """Record execution timing."""
        if stage not in self._timings:
            self._timings[stage] = []
        self._timings[stage].append(duration)
        measured = page_timings.get()
        if measured is not None:
            measured[stage] = measured.get(stage, 0) + duration

        # Check if we're approaching the timeout
        timeout = self._get_timeout(stage)
        if duration > timeout * 0.8:
            logger.warning(
                "Stage '%s' took %.1fs (timeout: %.1fs) - approaching limit",
                stage,
                duration,
                timeout,
            )

    @asynccontextmanager
    async def track(self, stage: str):
        """Track execution time and enforce timeout for a stage.

        The wrapped block runs under asyncio.timeout, so a stalled
        downstream call is cancelled and surfaces as StageTimeoutError
        instead of hanging the request. Callers convert that into a
        failed-stage result (see extraction_service).

        Requires Python 3.11+ (asyncio.timeout).

        Args:
            stage: Name of the pipeline stage.

        Raises:
            StageTimeoutError: If the stage exceeds its timeout.
        """
        timeout = self._get_timeout(stage)
        notify = stage_notifier.get()
        if notify:
            notify(stage)
        start = asyncio.get_event_loop().time()
        stage_token = stage_context.set(stage)
        deadline_token = stage_deadline.set(start + timeout)

        try:
            async with asyncio.timeout(timeout):
                yield
        except TimeoutError:
            raise StageTimeoutError(stage, timeout) from None
        finally:
            self._record_timing(stage, asyncio.get_event_loop().time() - start)
            stage_context.reset(stage_token)
            stage_deadline.reset(deadline_token)

    def get_timings(self) -> dict[str, list[float]]:
        """Get all recorded timings."""
        return dict(self._timings)

    def get_average(self, stage: str) -> float | None:
        """Get average execution time for a stage."""
        if stage not in self._timings or not self._timings[stage]:
            return None
        return sum(self._timings[stage]) / len(self._timings[stage])

    def reset(self) -> None:
        """Reset all timing data."""
        self._timings.clear()
