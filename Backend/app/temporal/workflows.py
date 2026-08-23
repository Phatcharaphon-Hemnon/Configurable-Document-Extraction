"""Temporal workflows for the document-extraction PoC."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

# Activities must be imported inside an ``imports_passed_through`` block so
# Temporal's deterministic-execution sandbox does not intercept the transitive
# imports (e.g. ``openai``, ``pydantic``) that are irrelevant to replay
# determinism.  See:
#   https://github.com/temporalio/samples-python
with workflow.unsafe.imports_passed_through():
    from app.temporal.activities import classify_document


@workflow.defn
class ClassifyDocumentWorkflow:
    """Proof-of-concept workflow that classifies a single document page.

    Invokes the ``classify_document`` activity with a Temporal-managed
    retry policy, replacing the manual ``try/except`` retry loops used in
    the current in-process pipeline.
    """

    @workflow.run
    async def run(self, filename: str, text_hint: str | None) -> dict:
        return await workflow.execute_activity(
            classify_document,
            args=[filename, text_hint],
            start_to_close_timeout=timedelta(seconds=90),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=2),
                backoff_coefficient=2.0,
                maximum_attempts=3,
                maximum_interval=timedelta(seconds=30),
            ),
        )
