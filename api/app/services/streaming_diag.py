"""Diagnostic-only streaming accumulation (never used by the pipeline).

Supports the bounded streaming diagnostic: incremental chunk timestamps,
read-idle vs overall deadline separation, partial-output classification,
and cancellation attribution — all content-free except the assembled
model-output text itself, which callers must NEVER log (a model may echo
document text) and must only parse/measure.

No prompt bodies, credentials, or document content enter events, errors,
or logs from this module.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


class StreamIdleTimeout(Exception):
    """No stream event arrived within the read-idle timeout."""

    def __init__(self, message: str, *, partial_text: str = "",
                 chunk_count: int = 0, elapsed: float = 0.0) -> None:
        super().__init__(message)
        self.partial_text = partial_text
        self.chunk_count = chunk_count
        self.elapsed = elapsed


class StreamOverallTimeout(Exception):
    """The finite overall wall-clock deadline expired mid-stream."""

    def __init__(self, message: str, *, partial_text: str = "",
                 chunk_count: int = 0, elapsed: float = 0.0) -> None:
        super().__init__(message)
        self.partial_text = partial_text
        self.chunk_count = chunk_count
        self.elapsed = elapsed


@dataclass
class StreamTimestamps:
    """Separate perf_counter stamps (seconds, monotonic).

    - ``dispatched_at``: when chunk collection started (collect entry).
    - ``established_at``: caller-supplied moment the SDK ``create`` call
      returned (SDK stream-establishment time). ``None`` means unknown —
      the SDK exposes no response-header hook, so this is NEVER a
      kernel-level header timestamp; a missing value is preserved as
      unknown, never inferred from chunk counts.
    - ``first_event_at``: first observed stream event (first successful
      ``__anext__``), including empty/heartbeat chunks.
    - ``first_content_at`` / ``completed_at``: first non-empty content /
      transport completion. Empty/heartbeat chunks advance arrival stats
      only, never content.
    """

    dispatched_at: float | None = None
    established_at: float | None = None
    first_event_at: float | None = None
    first_content_at: float | None = None
    completed_at: float | None = None


@dataclass
class StreamResult:
    """Accumulated stream outcome (transport-complete, not content-complete).

    ``complete`` is False when ``finish_reason == "length"`` (truncated by
    budget ⇒ incomplete by definition) even though transport ended
    normally. Parse validity is judged separately with the existing
    ``_try_parse_detailed`` contract — a parsed response alone never
    proves extraction accuracy.
    """

    text: str = ""
    chunk_count: int = 0
    empty_count: int = 0
    total_bytes: int = 0
    finish_reason: str | None = None
    complete: bool = False
    timestamps: StreamTimestamps = field(default_factory=StreamTimestamps)
    inter_arrival_s: list[float] = field(default_factory=list)


def _delta_content(chunk: Any) -> str | None:
    """Best-effort content extraction (SDK shape or test double)."""
    try:
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            return None
        delta = getattr(choices[0], "delta", None)
        content = getattr(delta, "content", None) if delta is not None else None
        return content if isinstance(content, str) else None
    except Exception:
        return None


def _finish_reason(chunk: Any) -> str | None:
    try:
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            return None
        reason = getattr(choices[0], "finish_reason", None)
        return reason if isinstance(reason, str) else None
    except Exception:
        return None


def remaining_overall(
    stage_deadline_s: float, stage_started_at: float, *, minimum_s: float = 1.0
) -> float:
    """Remaining overall budget for stream collection (seconds).

    An inner overall deadline measured from collect-entry can never fire
    before an outer stage deadline measured from pre-dispatch (it expires
    later by exactly the establishment duration), making it dead. Pass the
    remainder of the outer budget instead so ``StreamOverallTimeout``
    stays reachable and create-stall vs mid-stream-stall attribution
    remains possible. Never below ``minimum_s``.
    """
    return max(minimum_s, stage_deadline_s - (time.perf_counter() - stage_started_at))


async def collect_stream_chunks(
    stream: Any,
    *,
    idle_timeout: float,
    overall_deadline: float,
    established_at: float | None = None,
) -> StreamResult:
    """Accumulate one diagnostic stream with separated deadlines.

    - Read-idle timeout: no event for ``idle_timeout`` seconds ⇒
      ``StreamIdleTimeout`` carrying the partial assembly.
    - Overall wall-clock deadline: ``overall_deadline`` seconds from
      dispatch ⇒ ``StreamOverallTimeout`` carrying the partial assembly.
    - Outer cancellation propagates as ``CancelledError`` (never
      converted to a timeout); closing the client is NOT assumed to stop
      server computation.

    Streaming does not accelerate generation — same server compute,
    observability only.
    """
    result = StreamResult()
    timestamps = result.timestamps
    started = time.perf_counter()
    timestamps.dispatched_at = started
    timestamps.established_at = established_at
    last_arrival = started
    try:
        async with asyncio.timeout(overall_deadline):
            iterator = stream.__aiter__()
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        iterator.__anext__(), timeout=idle_timeout)
                except StopAsyncIteration:
                    break
                except TimeoutError as exc:
                    raise StreamIdleTimeout(
                        f"No stream event for {idle_timeout:.1f}s "
                        f"({result.chunk_count} chunks, "
                        f"{len(result.text)} chars assembled)",
                        partial_text=result.text,
                        chunk_count=result.chunk_count,
                        elapsed=time.perf_counter() - started,
                    ) from exc
                now = time.perf_counter()
                if timestamps.first_event_at is None:
                    timestamps.first_event_at = now
                result.inter_arrival_s.append(now - last_arrival)
                last_arrival = now
                result.chunk_count += 1
                content = _delta_content(chunk)
                if content:
                    if result.timestamps.first_content_at is None:
                        result.timestamps.first_content_at = now
                    result.text += content
                    result.total_bytes += len(content.encode("utf-8"))
                else:
                    result.empty_count += 1
                reason = _finish_reason(chunk)
                if reason:
                    result.finish_reason = reason
    except TimeoutError as exc:
        # Overall deadline (asyncio.timeout). An outer cancellation arriving
        # with the deadline must stay CancelledError, never a timeout.
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError() from None
        raise StreamOverallTimeout(
            f"Overall stream deadline {overall_deadline:.1f}s expired "
            f"({result.chunk_count} chunks, {len(result.text)} chars assembled)",
            partial_text=result.text,
            chunk_count=result.chunk_count,
            elapsed=time.perf_counter() - started,
        ) from exc
    timestamps.completed_at = time.perf_counter()
    result.complete = result.finish_reason != "length"
    return result


def single_dispatch_guard(create_fn):
    """Wrap one streaming ``create`` callable: exactly one dispatch.

    The first call passes through; any second call raises RuntimeError
    instead of issuing another request. No retries, no fallbacks, no
    corrective calls — callers surface provider rejections as
    explicitly reported limitations (never silently drop
    ``response_format``).
    """
    state = {"calls": 0}

    async def guarded(**kwargs):
        state["calls"] += 1
        if state["calls"] > 1:
            raise RuntimeError(
                "second streaming dispatch blocked: single-attempt diagnostic")
        return await create_fn(**kwargs)

    return guarded
