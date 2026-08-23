"""Temporal activities for the document-extraction PoC.

This module wraps existing agent logic as Temporal activities so the
workflow's retry policy — not ad-hoc try/except — governs retries.
"""

from __future__ import annotations

from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.services.sut_genai_client import SutGenAICallError as GeminiCallError


@activity.defn
async def classify_document(filename: str, text_hint: str | None) -> dict:
    """Classify a document by delegating to :class:`RouterAgent`.

    Returns a plain ``dict`` (not a ``RoutingDecision`` dataclass) so
    Temporal's default data-converter can round-trip the result as JSON.

    Limitations
    -----------
    * **Text only** — ``image_bytes`` support is deferred to a later
      milestone when the full extraction pipeline is migrated.
    * **No knowledge-base reconciliation** — ``knowledge_base=None`` and
      ``schema_mode="open"`` are hard-coded.  Strict-mode catalog
      reconciliation will be added once the full pipeline is orchestrated
      by Temporal.
    """
    # Construct Settings inside the function body (not at import time) so
    # the activity can be registered without triggering module-level side
    # effects in the worker process.
    from app.core.config import get_settings

    settings = get_settings()

    from app.agents.router import RouterAgent

    # No knowledge-base reconciliation in this PoC slice — see docstring.
    router = RouterAgent(settings, knowledge_base=None, schema_mode="open")

    try:
        decision = await router.classify(filename=filename, text_hint=text_hint)
    except GeminiCallError as exc:
        # Let the *workflow's* RetryPolicy govern retries — mark the error
        # as retryable so Temporal re-schedules the activity automatically.
        raise ApplicationError(str(exc), non_retryable=False) from exc

    return {
        "doc_type": decision.doc_type,
        "language": (
            decision.language.value
            if hasattr(decision.language, "value")
            else str(decision.language)
        ),
        "reason": decision.reason,
        "confidence": decision.confidence,
        "suggested_fields": [f.model_dump() for f in decision.suggested_fields],
        "out_of_catalog": decision.out_of_catalog,
    }
