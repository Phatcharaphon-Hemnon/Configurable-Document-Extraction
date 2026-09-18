"""Mocked-stream regressions for the diagnostic-only streaming path (offline).

No live inference, no real waits beyond fractions of a second. Covers:
timestamps (establishment/first-event/first-content/completion),
empty-chunk accounting, read-idle vs overall deadlines, partial-JSON
classification, finish_reason=length incompleteness, cancellation
attribution, format rejection without silent downgrade, and the
single-dispatch guard.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.services.provider_capabilities import build_chat_kwargs  # noqa: E402
from app.services.streaming_diag import (  # noqa: E402
    StreamIdleTimeout,
    StreamOverallTimeout,
    collect_stream_chunks,
    remaining_overall,
    single_dispatch_guard,
)


class _DummySchema(BaseModel):
    name: str
    value: int


def _chunk(content: str | None, finish: str | None = None):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            delta=SimpleNamespace(content=content),
            finish_reason=finish)],
        usage=None)


async def _stream(chunks, delays=None):
    delays = delays or [0.0] * len(chunks)
    for chunk, delay in zip(chunks, delays):
        if delay:
            await asyncio.sleep(delay)
        yield chunk


def _base_kwargs(**overrides):
    kwargs = dict(
        model="m", messages=[{"role": "user", "content": "hi"}],
        temperature=0.0, token_param="max_tokens", max_tokens=100,
        response_format={"type": "json_object"}, reasoning_effort="",
        disable_reasoning=True, omit_extra_body_reasoning=False)
    kwargs.update(overrides)
    return kwargs


def test_builder_default_has_no_stream_key():
    kwargs = build_chat_kwargs(
        model="m", messages=[{"role": "user", "content": "hi"}],
        temperature=0.0, token_param="max_tokens", max_tokens=100,
        response_format={"type": "json_object"}, reasoning_effort="",
        disable_reasoning=True, omit_extra_body_reasoning=False)
    assert "stream" not in kwargs
    assert "stream_options" not in kwargs


def test_builder_stream_opt_in_recorded():
    kwargs = build_chat_kwargs(
        model="m", messages=[{"role": "user", "content": "hi"}],
        temperature=0.0, token_param="max_tokens", max_tokens=100,
        response_format={"type": "json_object"}, reasoning_effort="",
        disable_reasoning=True, omit_extra_body_reasoning=False,
        stream=True)
    assert kwargs["stream"] is True
    assert "stream_options" not in kwargs  # usage stays unavailable by design


@pytest.mark.asyncio
async def test_timestamps_recorded_separately():
    chunks = [_chunk(None), _chunk(None), _chunk('{"name":'),
              _chunk(' "x", "value": 1}')]
    result = await collect_stream_chunks(
        _stream(chunks), idle_timeout=5.0, overall_deadline=5.0)
    assert result.text == '{"name": "x", "value": 1}'
    assert result.chunk_count == 4
    assert result.empty_count == 2
    ts = result.timestamps
    assert ts.dispatched_at is not None
    assert ts.first_event_at is not None
    assert ts.first_content_at is not None
    assert ts.first_content_at >= ts.first_event_at
    assert ts.completed_at is not None and ts.completed_at >= ts.first_content_at


@pytest.mark.asyncio
async def test_empty_chunks_are_not_progress():
    chunks = [_chunk(None)] * 5 + [_chunk("hi")]
    result = await collect_stream_chunks(
        _stream(chunks), idle_timeout=5.0, overall_deadline=5.0)
    assert result.empty_count == 5
    assert result.text == "hi"
    # First content comes after the empty run, not at stream start.
    assert result.timestamps.first_event_at is not None
    assert result.timestamps.first_content_at is not None
    assert result.timestamps.first_content_at >= result.timestamps.first_event_at


@pytest.mark.asyncio
async def test_idle_stall_preserves_partial():
    async def stalled():
        yield _chunk('{"name": "par')
        await asyncio.Event().wait()  # never resolves; idle must win

    with pytest.raises(StreamIdleTimeout) as exc_info:
        await collect_stream_chunks(
            stalled(), idle_timeout=0.2, overall_deadline=5.0)
    assert exc_info.value.partial_text == '{"name": "par'
    assert exc_info.value.chunk_count == 1


@pytest.mark.asyncio
async def test_overall_deadline_distinct_from_idle():
    async def slow_drip():
        while True:
            await asyncio.sleep(0.05)
            yield _chunk("x")

    with pytest.raises(StreamOverallTimeout):
        await collect_stream_chunks(
            slow_drip(), idle_timeout=5.0, overall_deadline=0.3)


@pytest.mark.asyncio
async def test_partial_json_classified_incomplete():
    from app.services.client import Client

    # Classification contract used for interrupted-stream assembly:
    # truncated/invalid assembly is incomplete even when a root-object
    # prefix exists. A parsed response alone never proves accuracy.
    parsed, diagnosis = Client._try_parse_detailed(_DummySchema, '{"name": "par')
    assert parsed is None
    assert diagnosis is not None and diagnosis.kind in (
        "truncated", "syntax_error", "empty", "wrong_root")


@pytest.mark.asyncio
async def test_finish_length_is_incomplete():
    chunks = [_chunk('{"name": "x", "value":', finish=None),
              _chunk(None, finish="length")]
    result = await collect_stream_chunks(
        _stream(chunks), idle_timeout=5.0, overall_deadline=5.0)
    assert result.finish_reason == "length"
    assert result.complete is False


@pytest.mark.asyncio
async def test_cancellation_is_not_timeout():
    async def hanging():
        await asyncio.Event().wait()
        yield _chunk("never")  # pragma: no cover

    async def run():
        await collect_stream_chunks(
            hanging(), idle_timeout=30.0, overall_deadline=30.0)

    task = asyncio.ensure_future(run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_format_rejection_stops_without_downgrade():
    from openai import BadRequestError

    calls: list[dict] = []

    async def rejecting_create(**kwargs):
        calls.append(kwargs)
        resp = MagicMock()
        resp.status_code = 400
        raise BadRequestError(
            message="response_format json_schema is not supported with stream",
            response=resp, body=None)

    guarded = single_dispatch_guard(rejecting_create)
    with pytest.raises(BadRequestError):
        await guarded(model="m", messages=[], response_format={"type": "json_schema"},
                      stream=True)
    # Exactly one dispatch; the caller must surface the limitation rather
    # than retrying without response_format.
    assert len(calls) == 1
    assert calls[0]["response_format"] == {"type": "json_schema"}
    assert calls[0]["stream"] is True


@pytest.mark.asyncio
async def test_second_dispatch_blocked():
    async def ok_create(**kwargs):
        async def gen():
            yield _chunk("x")

        return gen()

    guarded = single_dispatch_guard(ok_create)
    stream = await guarded(model="m")
    assert [c async for c in stream][0].choices[0].delta.content == "x"
    with pytest.raises(RuntimeError, match="second .* dispatch blocked"):
        await guarded(model="m")


@pytest.mark.asyncio
async def test_established_at_passes_through_unknown_preserved():
    import time

    marker = time.perf_counter()
    result = await collect_stream_chunks(
        _stream([_chunk("x")]), idle_timeout=5.0, overall_deadline=5.0,
        established_at=marker)
    assert result.timestamps.established_at == marker
    # Default callers supply nothing: unknown stays unknown, never inferred.
    result2 = await collect_stream_chunks(
        _stream([_chunk("x")]), idle_timeout=5.0, overall_deadline=5.0)
    assert result2.timestamps.established_at is None


@pytest.mark.asyncio
async def test_first_event_none_on_empty_stream():
    async def empty():
        return
        yield  # pragma: no cover — makes this an async generator

    result = await collect_stream_chunks(
        empty(), idle_timeout=5.0, overall_deadline=5.0)
    assert result.chunk_count == 0
    assert result.timestamps.dispatched_at is not None
    # No event observed: unknown, not zero-claimed.
    assert result.timestamps.first_event_at is None
    assert result.timestamps.first_content_at is None
    assert result.timestamps.completed_at is not None


def test_remaining_overall_is_stage_remainder():
    import time

    stage_start = time.perf_counter() - 10.0
    remaining = remaining_overall(150.0, stage_start)
    assert 139.0 < remaining < 141.0
    # Expired stage clamps to the minimum instead of zero/negative.
    old_start = time.perf_counter() - 200.0
    assert remaining_overall(150.0, old_start) == 1.0
    assert remaining_overall(150.0, old_start, minimum_s=2.5) == 2.5
