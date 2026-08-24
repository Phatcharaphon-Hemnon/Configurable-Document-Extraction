"""Temporal workflow: full extraction pipeline as durable activities.

Pipeline per page: classify → extract → validate → judge.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.schemas.documents import FileUploadMeta
    from app.temporal.activities import (
        classify_activity,
        extract_activity,
        judge_activity,
        parse_activity,
        validate_activity,
    )

_RETRY = RetryPolicy(maximum_attempts=3, initial_interval=2)


def _timeout(seconds: int) -> timedelta:
    return timedelta(seconds=seconds)


@workflow.defn
class ExtractDocumentWorkflow:
    @workflow.run
    async def run(
        self,
        filename: str,
        raw_content: bytes,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        page_texts: list[str] = await workflow.execute_activity(
            parse_activity,
            args=[filename, raw_content],
            start_to_close_timeout=_timeout(300),
            retry_policy=_RETRY,
        )

        results: list[dict[str, Any]] = []
        for page_text in page_texts or [""]:
            doc = await workflow.execute_activity(
                classify_activity,
                args=[filename, page_text],
                start_to_close_timeout=_timeout(180),
                retry_policy=_RETRY,
            )
            extracted = await workflow.execute_activity(
                extract_activity,
                args=[filename, page_text, doc["doc_type"]],
                start_to_close_timeout=_timeout(300),
                retry_policy=_RETRY,
            )
            validated = await workflow.execute_activity(
                validate_activity,
                args=[extracted, page_text],
                start_to_close_timeout=_timeout(120),
                retry_policy=_RETRY,
            )
            judged = await workflow.execute_activity(
                judge_activity,
                args=[validated, page_text],
                start_to_close_timeout=_timeout(300),
                retry_policy=_RETRY,
            )
            results.append(judged)

        return {
            "request": FileUploadMeta(filename=filename, content_type=content_type).model_dump(mode="json"),
            "documents": results,
        }
