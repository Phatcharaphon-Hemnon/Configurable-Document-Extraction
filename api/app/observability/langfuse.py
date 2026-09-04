"""Langfuse LLM observability wrapper (SDK v4 API).

One trace per extracted page: ``extract-document`` root span with nested
child observations — ``classify-document`` / ``extract-fields`` /
``judge-extraction`` as generations (model + token usage, so Langfuse
computes cost), ``ocr-page`` / ``validate-fields`` / ``catalog-update`` as
spans — plus trace-level scores (judge-score, completeness, needs-review).

Privacy: document text carries PII (names, phones, tax IDs). The SDK-level
``mask`` hook redacts detectable PII patterns and truncates long strings
before anything leaves the host. Tracing is strictly opt-in: without
``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` every method is a no-op.

Follows https://langfuse.com/docs/observability/best-practices
(descriptive verb-first names, proper nesting, generation types with
model/tokens, meaningful trace input/output, metadata over dynamic names).
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import Settings

logger = logging.getLogger(__name__)

# Bound trace payloads: excerpts stay readable, full texts stay out.
_MAX_TEXT_CHARS = 2000


def _truncate_text(value: Any, limit: int = _MAX_TEXT_CHARS) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"… [truncated, {len(value)} chars total]"
    return value


def build_mask_function(detector: Any | None) -> Any | None:
    """Build an SDK ``mask`` hook from the project's PII detector.

    Redacts detectable PII patterns in every string payload and truncates
    long strings. Never raises: on any failure the data passes through
    unchanged (availability over redaction — failures are logged).
    """
    if detector is None:
        return None

    def _walk(node: Any) -> Any:
        try:
            if isinstance(node, str):
                text = _truncate_text(node)
                try:
                    return detector.redact(text)
                except Exception:
                    return text
            if isinstance(node, dict):
                return {key: _walk(value) for key, value in node.items()}
            if isinstance(node, (list, tuple)):
                return [_walk(item) for item in node]
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Trace mask walk failed: %s", exc)
        return node

    def _mask(*, data: Any, **kwargs: Any) -> Any:
        del kwargs
        try:
            return _walk(data)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Trace masking failed: %s", exc)
            return data

    return _mask


class LangfuseTracer:
    """Thin, no-op-safe facade over the Langfuse v4 SDK.

    Usage:
        tracer = LangfuseTracer(settings)
        trace = tracer.start_trace("extract-document", input={...}, metadata={...})
        gen = trace.generation("classify-document", model="...", input={...})
        gen.end(output={...}, usage={"input": n, "output": m})
        trace.score("completeness", 1.0)
        tracer.flush()
    """

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._client = client
        if self._client is None and settings.langfuse_enabled:
            try:
                from langfuse import Langfuse

                mask = None
                if getattr(settings, "pii_detection_enabled", False):
                    try:
                        from app.guards.pii_detector import PIIDetector

                        mask = build_mask_function(PIIDetector())
                    except Exception as exc:
                        logger.warning("PII mask unavailable, tracing unmasked: %s", exc)
                self._client = Langfuse(
                    public_key=settings.langfuse_public_key,
                    secret_key=settings.langfuse_secret_key,
                    host=settings.langfuse_host,
                    environment=getattr(settings, "app_env", "development"),
                    mask=mask,
                )
                logger.info("Langfuse enabled (host=%s)", settings.langfuse_host)
            except Exception as exc:  # pragma: no cover - optional dependency
                logger.warning("Langfuse init failed, continuing without tracing: %s", exc)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def start_trace(
        self,
        name: str,
        input_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> _Trace:
        if self._client is None:
            return _Trace(None)
        try:
            root = self._client.start_observation(
                name=name,
                as_type="span",
                input=input_data or {},
                metadata=metadata or {},
            )
            return _Trace(self._client, root)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Langfuse start_trace failed: %s", exc)
            return _Trace(None)

    def flush(self) -> None:
        if self._client is not None:
            try:
                self._client.flush()
            except Exception:  # pragma: no cover
                pass


class _Trace:
    """Handle for one trace; spawns nested child observations.

    Children are created via the root handle's ``start_observation`` so the
    backend nests them under the root span in a single trace. (Passing an
    explicit ``trace_context`` was verified to orphan each observation into
    its own trace — do not reintroduce it.)
    """

    def __init__(self, client: Any | None, root: Any | None = None) -> None:
        self._client = client
        self._root = root

    @property
    def id(self) -> str | None:
        if self._root is None:
            return None
        return getattr(self._root, "trace_id", None)

    def _spawn(
        self,
        name: str,
        as_type: str,
        model: str | None = None,
        input_data: dict | None = None,
        output: dict | None = None,
        metadata: dict | None = None,
        level: str | None = None,
    ) -> _Span:
        if self._client is None or self._root is None:
            return _Span(None)
        try:
            kwargs: dict[str, Any] = {
                "name": name,
                "as_type": as_type,
                "input": input_data or {},
                "output": output or {},
                "metadata": metadata or {},
            }
            if model is not None:
                kwargs["model"] = model
            if level is not None:
                kwargs["level"] = level
            return _Span(self._root.start_observation(**kwargs))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Langfuse observation %r failed: %s", name, exc)
            return _Span(None)

    def span(
        self,
        name: str,
        input_data: dict | None = None,
        output: dict | None = None,
        metadata: dict | None = None,
        level: str | None = None,
    ) -> _Span:
        """Complete nested span for a deterministic step.

        Fire-and-forget: the observation is ended immediately, because
        un-ended spans are never exported. For LLM calls needing post-call
        data (output, token usage), use generation() + end() instead.
        """
        handle = self._spawn(name, "span", input_data=input_data, output=output,
                             metadata=metadata, level=level)
        handle.end()
        return handle

    def generation(
        self,
        name: str,
        model: str | None = None,
        input_data: dict | None = None,
        metadata: dict | None = None,
    ) -> _Span:
        """Nested generation for one LLM call. End with output + usage."""
        return self._spawn(name, "generation", model=model,
                           input_data=input_data, metadata=metadata)

    def score(self, name: str, value: float | str, comment: str | None = None) -> None:
        """Trace-level score (judge verdicts, quality metrics)."""
        if self._client is None or self._root is None:
            return
        try:
            self._root.score_trace(name=name, value=value, comment=comment)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Langfuse score %r failed: %s", name, exc)

    def update(self, output: Any = None, metadata: dict | None = None) -> None:
        if self._client is None or self._root is None:
            return
        try:
            self._root.update(output=output, metadata=metadata or {})
        except Exception:  # pragma: no cover
            pass

    def set_io(self, input_data: Any = None, output_data: Any = None) -> None:
        """Set trace-level input/output shown in the tracing table."""
        if self._client is None or self._root is None:
            return
        try:
            self._root.set_trace_io(input=input_data, output=output_data)
        except Exception:  # pragma: no cover
            pass

    def end(self, level: str | None = None) -> None:
        """End the root span. Un-ended roots are never exported, so every
        return path in the pipeline must call this (after set_io/scores)."""
        if self._client is None or self._root is None:
            return
        try:
            if level is not None:
                self._root.update(level=level)
            self._root.end()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Langfuse trace end failed: %s", exc)


class _Span:
    """Handle for one nested observation; end() records output + usage."""

    def __init__(self, handle: Any | None) -> None:
        self._handle = handle

    def end(
        self,
        output: dict | None = None,
        metadata: dict | None = None,
        usage: dict[str, int] | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        if self._handle is None:
            return
        try:
            kwargs: dict[str, Any] = {}
            if output is not None:
                kwargs["output"] = output
            if metadata is not None:
                kwargs["metadata"] = metadata
            if usage is not None:
                kwargs["usage_details"] = {
                    key: int(value)
                    for key, value in usage.items()
                    if isinstance(value, (int, float)) and value is not None
                }
            if level is not None:
                kwargs["level"] = level
            if status_message is not None:
                kwargs["status_message"] = status_message
            if kwargs:
                self._handle.update(**kwargs)
            self._handle.end()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Langfuse span end failed: %s", exc)

    def update(self, output: Any = None, metadata: dict | None = None) -> None:
        if self._handle is None:
            return
        try:
            self._handle.update(output=output, metadata=metadata or {})
        except Exception:  # pragma: no cover
            pass
