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
    from app.temporal.activities import parse_detailed_activity, process_page_activity

_RETRY = RetryPolicy(maximum_attempts=1)


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
        # Detailed parse keeps hybrid OCR uncertainty (review reasons) with
        # the text; only the selected text enters extraction.
        pages: list[Any] = await workflow.execute_activity(
            parse_detailed_activity,
            args=[filename, raw_content],
            start_to_close_timeout=_timeout(300),
            retry_policy=_RETRY,
        )

        results: list[dict[str, Any]] = []
        for page in pages or [{"text": "", "review_reasons": []}]:
            if isinstance(page, str):  # backward compat with old parse_activity
                page_text, reviews = page, []
            else:
                page_text, reviews = page.get("text", ""), page.get("review_reasons", [])
            judged = await workflow.execute_activity(
                process_page_activity,
                args=[filename, page_text, reviews],
                start_to_close_timeout=_timeout(600),
                retry_policy=_RETRY,
            )
            results.append(judged)

        return {
            "request": FileUploadMeta(filename=filename, content_type=content_type).model_dump(mode="json"),
            "documents": results,
        }


@workflow.defn
class ExtractPageWorkflow:
    @workflow.run
    async def run(
        self, filename: str, page_text: str, ocr_review_reasons: list[str] | None = None,
    ) -> dict[str, Any]:
        # Provider retries belong to the shared four-attempt client budget.
        # Do not multiply LLM attempts with activity retries.
        # ocr_review_reasons is optional (extract_group merges reviews itself,
        # so the production path passes nothing here).
        return await workflow.execute_activity(process_page_activity, args=[filename, page_text, ocr_review_reasons or []],
            start_to_close_timeout=_timeout(600), retry_policy=RetryPolicy(maximum_attempts=1))
