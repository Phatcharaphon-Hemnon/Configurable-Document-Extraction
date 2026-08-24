"""Langfuse LLM observability wrapper.

Disabled automatically when LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are
missing, so local development never requires a Langfuse account.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import Settings

logger = logging.getLogger(__name__)


class LangfuseTracer:
    """Thin wrapper around the Langfuse SDK.

    Usage:
        tracer = LangfuseTracer(settings)
        trace = tracer.start_trace(name="extract", input={...})
        trace.span(name="router", output={...})
        ...
        tracer.flush()
    All methods are safe no-ops when Langfuse is not configured.
    """

    def __init__(self, settings: Settings) -> None:
        self._client = None
        if settings.langfuse_enabled:
            try:
                from langfuse import Langfuse

                self._client = Langfuse(
                    public_key=settings.langfuse_public_key,
                    secret_key=settings.langfuse_secret_key,
                    host=settings.langfuse_host,
                )
                logger.info("Langfuse enabled (host=%s)", settings.langfuse_host)
            except Exception as exc:  # pragma: no cover - optional dependency
                logger.warning("Langfuse init failed, continuing without tracing: %s", exc)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def start_trace(self, name: str, input_data: dict[str, Any] | None = None) -> _Trace:
        if self._client is None:
            return _Trace(None)
        try:
            return _Trace(self._client.trace(name=name, input=input_data or {}))
        except Exception:  # pragma: no cover
            return _Trace(None)

    def flush(self) -> None:
        if self._client is not None:
            try:
                self._client.flush()
            except Exception:  # pragma: no cover
                pass


class _Trace:
    """No-op safe trace handle."""

    def __init__(self, trace: Any) -> None:
        self._trace = trace

    def span(self, name: str, input_data: dict | None = None, output: dict | None = None,
             metadata: dict | None = None) -> None:
        if self._trace is None:
            return
        try:
            self._trace.span(name=name, input=input_data or {}, output=output or {}, metadata=metadata or {})
        except Exception:  # pragma: no cover
            pass

    def update(self, output: Any = None, metadata: dict | None = None) -> None:
        if self._trace is None:
            return
        try:
            self._trace.update(output=output, metadata=metadata or {})
        except Exception:  # pragma: no cover
            pass
