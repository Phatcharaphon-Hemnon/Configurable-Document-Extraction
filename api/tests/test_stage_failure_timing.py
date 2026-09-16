"""Provider failure latency must remain visible in per-page timing."""
import asyncio

import pytest

from app.guards.timeout_guard import StageTimeoutConfig, StageTimeoutError, TimeoutGuard, page_timings


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [ValueError('provider failed'), asyncio.CancelledError()])
async def test_failed_stage_records_time_once_and_restores_context(failure):
    from app.services.request_control import stage_context
    guard = TimeoutGuard()
    measured = {}
    token = page_timings.set(measured)
    before = stage_context.get()
    try:
        with pytest.raises(type(failure)):
            async with guard.track('extractor'):
                raise failure
        assert measured['extractor'] >= 0
        assert len(guard.get_timings()['extractor']) == 1
        assert stage_context.get() == before
    finally:
        page_timings.reset(token)


@pytest.mark.asyncio
async def test_deadline_records_single_timing():
    guard = TimeoutGuard(StageTimeoutConfig(extractor_seconds=.01))
    with pytest.raises(StageTimeoutError):
        async with guard.track('extractor'):
            await asyncio.sleep(1)
    assert len(guard.get_timings()['extractor']) == 1
