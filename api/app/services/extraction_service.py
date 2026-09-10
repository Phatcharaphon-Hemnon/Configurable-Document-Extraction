"""Extraction orchestration: upload → RapidOCR → Router → Extractor →
Validator → Judge → (auto-eval). One document per result; multi-page and
multi-document uploads produce one result per page/document."""

from __future__ import annotations

import asyncio
import logging
import time
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from app.agents.extractors import build_extractors
from app.agents.judge import JUDGE_PASS_SCORE, JudgeAgent
from app.agents.router import RouterAgent
from app.agents.validator import ValidatorAgent
from app.core.config import Settings
from app.core.security import is_ocr_text_coherent, is_verbatim_span, sanitize_document_text
from app.guards.audit_logger import AuditLogger
from app.guards.content_guard import ContentLimits, validate_document_text
from app.guards.pii_detector import PIIDetector
from app.guards.timeout_guard import StageTimeoutConfig, TimeoutGuard, stage_notifier
from app.observability.langfuse import LangfuseTracer
from app.schemas.documents import (
    BatchCreateResponse,
    BatchStatusResponse,
    EvaluateResponse,
    ExtractedField,
    ExtractionResult,
    FileExtractionResponse,
    FileUploadMeta,
    ProviderErrorDetails,
)
from app.services.client import ClientError
from app.services.field_catalog import register_discovered_fields
from app.services.field_matching import values_match
from app.services.job_store import InMemoryJobStore, JobRecord, SQLiteJobStore
from app.services.knowledge_base import KnowledgeBaseRepository
from app.services.local_ocr import LocalOCRClient, OCRPage
from app.services.request_control import job_context
from app.services.source_storage import SourceStorage

logger = logging.getLogger(__name__)
queue_wait: ContextVar[float] = ContextVar("queue_wait", default=0)


def _judge_demands_review(judge_result) -> bool:
    """True when the judge reports a real problem.

    `info`-severity issues are confirmations ("value X is supported"),
    not problems — only `warning`/`error` issues or a sub-threshold score
    keep a page in review.
    """
    if judge_result is None:
        return False
    if judge_result.score < JUDGE_PASS_SCORE:
        return True
    return any(getattr(i, "severity", "warning") in ("warning", "error")
               for i in (judge_result.issues or []))


def _is_numeric_value(value: object) -> bool:
    """True for numbers and decimal-formatted numeric strings (not IDs/dates)."""
    import re as _re

    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return bool(_re.fullmatch(r"-?\d+[,.]\d[\d,.]*", value.strip()))
    return False


def _all_numeric_spans_verbatim(fields: list[ExtractedField], tables: list, page_text: str | None) -> bool:
    """Judge-skip gate: every numeric field/cell span must be a contiguous
    page-text substring. A confidently-wrong number with a paraphrased span
    must always get an independent judge review (never skip)."""
    if not page_text:
        return False
    spans = [
        (f.value, f.source_span)
        for f in fields
        if f.value is not None and f.source_span
    ]
    for table in tables or []:
        for row in table.rows:
            for cell in row:
                if cell.value is not None and cell.source_span:
                    spans.append((cell.value, cell.source_span))
    for value, span in spans:
        if _is_numeric_value(value) and not is_verbatim_span(span, page_text):
            return False
    return True


def _provider_error_details(
    exc: BaseException,
    *,
    stage: str,
    model: str | None,
    provider: str | None,
) -> ProviderErrorDetails | None:
    """Walk the exception chain for ClientError.provider_details.

    Returns None when no provider call was involved (e.g. OCR/validator
    failures) so UI <details> only appears for real gateway errors.
    """
    seen: ProviderErrorDetails | None = None
    current: BaseException | None = exc
    while current is not None:
        details = getattr(current, "provider_details", None)
        if isinstance(details, dict) and details:
            seen = ProviderErrorDetails(
                stage=stage,
                provider=provider,
                model=model,
                error_type=details.get("error_type"),
                status=details.get("status"),
                code=details.get("code"),
                param=details.get("param"),
                type=details.get("type"),
                message=details.get("message"),
                request_id=details.get("request_id"),
            )
            break
        current = current.__cause__ or current.__context__
    if seen is None and isinstance(exc, ClientError):
        # Timeout/budget ClientErrors without SDK body still get context.
        seen = ProviderErrorDetails(stage=stage, provider=provider, model=model)
    return seen

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif"}
_IMAGE_CONTENT_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "image/bmp", "image/tiff",
}


def _is_image_file(filename: str, content_type: str | None) -> bool:
    ext = Path(filename or "").suffix.lower()
    return ext in _IMAGE_EXTENSIONS or (content_type in _IMAGE_CONTENT_TYPES)


def _image_media_type(filename: str, content_type: str | None) -> str:
    ext = Path(filename or "").suffix.lower()
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif",
        ".webp": "image/webp", ".bmp": "image/bmp",
        ".tiff": "image/tiff", ".tif": "image/tiff",
    }.get(ext, "image/jpeg")


def _agent_usage(agent: object) -> dict[str, int] | None:
    """Token counts from an agent's last LLM call, for trace generations.

    Read synchronously right after awaiting the agent (same task, no await
    in between) so concurrent pages can't interleave. Returns None for
    mocked agents or calls without usage data.
    """
    usage = getattr(getattr(agent, "_client", None), "last_usage", None)
    if not isinstance(usage, dict):
        return None
    cleaned = {
        key: int(value)
        for key, value in usage.items()
        if isinstance(value, (int, float)) and value is not None
    }
    return cleaned or None


class UploadedFilePart:
    """One raw uploaded file, before it's been split into pages."""

    def __init__(self, filename: str, content_type: str | None, raw_content: bytes) -> None:
        self.filename = filename
        self.content_type = content_type
        self.raw_content = raw_content


class DocumentExtractionService:
    def __init__(self, settings: Settings, job_store: InMemoryJobStore | None = None) -> None:
        self.settings = settings
        self._job_lock = asyncio.Lock()
        self.knowledge_base = KnowledgeBaseRepository(Path(settings.knowledge_base_path))
        self.catalog = self.knowledge_base.catalog
        # Local OCR (RapidOCR, ONNX): handles both PDFs (rendered at OCR_DPI
        # via PyMuPDF) and images. Runs fully offline — no API key, no network.
        self.ocr = LocalOCRClient(settings)
        self.sources = SourceStorage(settings.source_storage_path)
        self.tracer = LangfuseTracer(settings)
        self.router = RouterAgent(settings)
        self.extractors = build_extractors(settings, self.catalog)
        self.validator = ValidatorAgent(self.catalog)
        self.judge = JudgeAgent(settings)

        # Use SQLite job store if enabled, otherwise use in-memory
        if settings.database_enabled:
            self.job_store = SQLiteJobStore(db_path=settings.database_path)
            logger.info("Using SQLite job store: %s", settings.database_path)
        else:
            self.job_store = job_store or InMemoryJobStore()
            logger.info("Using in-memory job store")

        # Boot-cleanup: rows left 'queued' or 'processing' belong to requests killed before
        # saving (server restart / client disconnect) — no live worker can
        # own them, so mark them failed for a truthful History tab.
        try:
            orphaned = self.job_store.fail_stale_queued()
            if orphaned:
                logger.info("Marked %d orphaned pending job(s) as failed", orphaned)
        except Exception as exc:
            logger.warning("Stale-job cleanup skipped: %s", exc)

        # Initialize guards
        self.audit_logger = AuditLogger(
            log_file=settings.audit_log_file if settings.audit_log_enabled else None
        )
        self.timeout_guard = TimeoutGuard(
            StageTimeoutConfig(
                router_seconds=settings.router_timeout_seconds,
                extractor_seconds=settings.extractor_timeout_seconds,
                judge_seconds=settings.judge_timeout_seconds,
                ocr_seconds=settings.ocr_timeout_seconds,
            )
        )
        self.pii_detector = PIIDetector(
            redact_pii=settings.pii_redact_in_logs
        )
        self.content_limits = ContentLimits(
            max_document_chars=settings.max_document_chars,
            max_prompt_chars=settings.max_prompt_chars,
        )

    # ------------------------------------------------------------------
    # Templates
    # ------------------------------------------------------------------

    def list_templates(self) -> list[dict[str, Any]]:
        return self.knowledge_base.list_templates()

    # ------------------------------------------------------------------
    # Evaluation (exact catalog-name matching, no aliases)
    # ------------------------------------------------------------------

    def evaluate(
        self,
        prediction: dict[str, Any],
        ground_truth: dict[str, Any],
        source_text: str | None = None,
        doc_type: str | None = None,
    ) -> EvaluateResponse:
        matched = 0
        false_positives = 0
        false_negatives = 0

        mismatches = [
            {"field": name, "predicted": prediction.get(name), "expected": expected}
            for name, expected in ground_truth.items()
            if not values_match(prediction.get(name), expected)
        ]

        for name, expected in ground_truth.items():
            if values_match(prediction.get(name), expected):
                matched += 1
            else:
                false_negatives += 1

        for name, predicted in prediction.items():
            if not values_match(predicted, ground_truth.get(name)):
                false_positives += 1

        precision = matched / (matched + false_positives) if (matched + false_positives) else 1.0
        recall = matched / (matched + false_negatives) if (matched + false_negatives) else 1.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

        return EvaluateResponse(
            score=f1,
            precision=precision,
            recall=recall,
            f1=f1,
            summary="Evaluation completed",
            mismatches=mismatches,
        )

    # ------------------------------------------------------------------
    # Multi-file / multi-page extraction
    # ------------------------------------------------------------------

    async def extract_group(
        self,
        parts: list[UploadedFilePart],
        job_id: UUID | None = None,
    ) -> FileExtractionResponse:
        total_size = sum(len(p.raw_content) for p in parts)
        combined_name = parts[0].filename if len(parts) == 1 else f"{len(parts)} files ({parts[0].filename}, ...)"
        request_meta = FileUploadMeta(
            filename=combined_name,
            content_type=parts[0].content_type if len(parts) == 1 else None,
            size_bytes=total_size,
        )

        # Use the caller's job row when running as a background task
        # (created up-front so the client can poll); otherwise create one.
        if job_id is None:
            job = self.job_store.create(
                filename=combined_name,
                content_type=request_meta.content_type,
                size_bytes=total_size,
            )
        else:
            job = JobRecord(job_id=job_id, status="queued")
        job_id_str = str(job.job_id)

        started = time.perf_counter()
        documents = []
        file_errors = []
        progress = {"completed_pages": 0, "total_pages": 0, "stage": "ocr"}
        try:
            for part in parts:
                source_id = self.sources.save(part.filename, part.raw_content, part.content_type)
                self.job_store.set_progress(job.job_id, progress)
                try:
                    parsed = await self.ocr.aparse_file(part.raw_content, part.filename)
                except Exception as exc:
                    file_errors.append(f"{part.filename}: Failed to OCR: {exc}")
                    continue
                details = getattr(self.ocr, "last_pages", [])
                if not isinstance(details, list) or len(details) != len(parsed):
                    details = [OCRPage(text=text) for text in parsed]
                progress["total_pages"] += len(parsed)
                for page_index, (page_text, ocr_page) in enumerate(zip(parsed, details), 1):
                    source = self.sources.reference(source_id, page_index, len(parsed), ocr_page.preview)
                    def notify_stage(stage):
                        progress["stage"] = stage
                        self.job_store.set_progress(job.job_id, progress)
                    stage_token = stage_notifier.set(notify_stage)
                    try:
                        if ocr_page.error or not page_text.strip():
                            message = ocr_page.error or "No readable text on this page"
                            document = ExtractionResult(doc_type="invoice", fields=[], needs_review=True,
                                completeness_score=0, error=message, failed_stage="ocr", validation_errors=[message])
                        elif self.settings.temporal_enabled:
                            from app.temporal.client import get_temporal_client
                            client = await get_temporal_client()
                            raw = await client.execute_workflow("ExtractPageWorkflow", args=[part.filename, page_text],
                                id=f"{job.job_id}-page-{len(documents) + 1}", task_queue=self.settings.temporal_task_queue)
                            document = ExtractionResult.model_validate(raw)
                        else:
                            document = await self._extract_one_page(filename=part.filename, page_text=page_text)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        document = ExtractionResult(doc_type="invoice", fields=[], needs_review=True,
                            completeness_score=0, error=f"Page pipeline failed: {exc}", validation_errors=[str(exc)])
                    finally:
                        stage_notifier.reset(stage_token)
                    document.source = source
                    document.ocr_blocks = ocr_page.blocks
                    document.full_text = page_text
                    document.timings.update(ocr=ocr_page.seconds, render=ocr_page.render_seconds,
                                            ocr_cached=float(ocr_page.cached))
                    documents.append(document)
                    self.job_store.save_result(job.job_id, FileExtractionResponse(
                        request=request_meta, documents=documents, job_id=job_id_str,
                        file_errors=file_errors).model_dump(mode="json"), status="processing")
                    progress["completed_pages"] += 1
                    self.job_store.set_progress(job.job_id, progress)
            response = FileExtractionResponse(request=request_meta, documents=documents, job_id=job_id_str,
                file_errors=file_errors, error="; ".join(file_errors) if not documents else None,
                timings={"processing": time.perf_counter() - started, "queue": queue_wait.get()})
            if not documents and not response.error:
                response.error = "No readable pages found"
            progress["stage"] = "completed" if documents else "failed"
            self.job_store.set_progress(job.job_id, progress)
            self.job_store.save_result(job.job_id, response.model_dump(mode="json"))
            return response
        except asyncio.CancelledError:
            self.job_store.fail_job(job.job_id, "Cancelled (server shutdown)")
            raise
        except Exception as exc:
            self.job_store.fail_job(job.job_id, str(exc))
            raise

    # ------------------------------------------------------------------
    # Single page pipeline
    # ------------------------------------------------------------------

    async def _extract_one_page(self, filename: str, page_text: str, **kwargs) -> ExtractionResult:
        from app.guards.timeout_guard import page_timings
        measured: dict[str, float] = {}
        token = page_timings.set(measured)
        started = time.perf_counter()
        agents = {"router": self.router, "judge": self.judge}
        agents.update({f"extractor_{key}": agent for key, agent in self.extractors.items()})
        for agent in agents.values():
            client = getattr(agent, "_client", None)
            if client is not None:
                client.last_usage = None
                client.last_attempts = 0
        try:
            result = await self._extract_page_impl(filename, page_text, **kwargs)
            result.timings.update(measured, pipeline=time.perf_counter() - started)
            for name, agent in agents.items():
                client = getattr(agent, "_client", None)
                attempts = getattr(client, "last_attempts", 0)
                usage = _agent_usage(agent) or {}
                if isinstance(attempts, int) and attempts:
                    usage["attempts"] = attempts
                    usage["retries"] = max(0, attempts - 1)
                if usage:
                    result.usage[name] = usage
            return result
        finally:
            page_timings.reset(token)

    async def _extract_page_impl(
        self,
        filename: str,
        page_text: str,
        image_bytes: bytes | None = None,
        image_media_type: str | None = None,
    ) -> ExtractionResult:
        trace = self.tracer.start_trace(
            "extract-document",
            input_data={
                "filename": filename,
                "text_chars": len(page_text or ""),
                "text_excerpt": (page_text or "")[:1000],
            },
            metadata={
                "has_image": bool(image_bytes),
                "router_model": self.settings.router_model_name,
                "extraction_model": self.settings.extraction_model_name,
                "judge_model": self.settings.judge_model_name,
            },
        )

        # Track extraction source. All text now comes from local RapidOCR,
        # so the source is "ocr". `image_bytes`/`image_media_type` are kept
        # only for backward compatibility (old callers/tests); when text is
        # empty but image bytes are provided we OCR them here instead of
        # using a vision model.
        _ = image_media_type  # legacy kwarg, intentionally unused (text-only pipeline)
        has_text = bool(page_text and page_text.strip())
        extraction_source: str | None = None
        ocr_performed = False

        if not has_text and image_bytes:
            try:
                async with self.timeout_guard.track("ocr"):
                    ocr_pages = await self.ocr.aparse_file(image_bytes, filename)
                page_text = "\n\n".join(p for p in ocr_pages if p.strip())
                has_text = bool(page_text.strip())
                ocr_performed = bool(has_text)
                if ocr_performed:
                    logger.info("RapidOCR recovered text for %s (%d chars)", filename, len(page_text))
                    trace.span("ocr-page", output={"chars": len(page_text), "fallback": True})
            except Exception as exc:
                logger.warning("RapidOCR image fallback failed for %s: %s", filename, exc)

        # Apply content size limits
        if self.settings.guards_enabled and page_text:
            page_text = validate_document_text(
                page_text,
                self.content_limits,
                truncate=True,
            )

        # --- 1. Router (text-only; page_text comes from RapidOCR) ---
        router_gen = trace.generation(
            "classify-document",
            model=self.settings.router_model_name,
            input_data={"filename": filename, "text_excerpt": (page_text or "")[:1000]},
        )
        try:
            async with self.timeout_guard.track("router"):
                routing = await self.router.classify(
                    filename=filename,
                    text_hint=sanitize_document_text(page_text) or None,
                )
            router_gen.end(
                output={"doc_type": routing.doc_type, "confidence": routing.confidence,
                        "reason": routing.reason},
                usage=_agent_usage(self.router),
            )
        except Exception as exc:
            details = _provider_error_details(
                exc,
                stage="router",
                model=self.settings.router_model_name,
                provider=self.settings.llm_provider,
            )
            router_gen.end(
                output={"error": str(exc), "provider": details.model_dump() if details else None},
                level="ERROR",
                status_message=str(exc)[:500],
            )
            trace.end(level="ERROR")
            self.tracer.flush()
            if details is not None:
                logger.error(
                    "Router provider error model=%s status=%s code=%s request_id=%s message=%.2000s",
                    details.model, details.status, details.code,
                    details.request_id, details.message or "",
                )
            return ExtractionResult(
                doc_type="invoice",  # placeholder; overwritten below by schema-safe error path
                fields=[],
                validation_errors=[f"Router failed: {exc}"],
                needs_review=True,
                completeness_score=0.0,
                error=f"Router failed: {exc}",
                failed_stage="router",
                error_details=details,
            )

        # --- 2. Extractor (per doc type) ---
        # Fail fast on degenerate OCR text: a small model stalls ~2000s
        # (full retry budget) on script-salad scans instead of extracting.
        # An honest flagged error beats a timeout burn with no data.
        if page_text and not is_ocr_text_coherent(page_text):
            trace.span("validate-fields", output={"error": "ocr text incoherent"}, level="ERROR")
            trace.end(level="ERROR")
            self.tracer.flush()
            return self._failed(
                routing,
                "OCR text incoherent: too fragmented for reliable extraction "
                "(rescan at higher DPI or review manually)",
                "ocr",
            )
        few_shot: list[dict] | None = None
        limit = self.settings.few_shot_examples_per_doc_type
        if limit > 0:
            few_shot = self.knowledge_base.get_few_shot_examples(routing.doc_type, limit=limit) or None

        extract_gen = trace.generation(
            "extract-fields",
            model=self.settings.extraction_model_name,
            input_data={"doc_type": routing.doc_type,
                        "catalog_fields": len(self.catalog.get_field_names(routing.doc_type)),
                        "text_chars": len(page_text or "")},
        )
        try:
            async with self.timeout_guard.track("extractor"):
                fields, new_field_names = await self.extractors[routing.doc_type].extract(
                    text=page_text or "",
                    few_shot=few_shot,
                )
            if not fields and not getattr(self.extractors[routing.doc_type], "last_tables", []):
                raise ValueError("No usable fields were extracted from the OCR text. "
                                 "Check the OCR text or retry extraction with a suitable text model.")
            extract_gen.end(
                output={"fields": [{"name": f.name, "confidence": f.confidence} for f in fields],
                        "new_fields": new_field_names},
                usage=_agent_usage(self.extractors[routing.doc_type]),
            )
        except Exception as exc:
            details = _provider_error_details(
                exc,
                stage="extractor",
                model=self.settings.extraction_model_name,
                provider=self.settings.llm_provider,
            )
            extract_gen.end(
                output={"error": str(exc), "provider": details.model_dump() if details else None},
                level="ERROR",
                status_message=str(exc)[:500],
            )
            trace.end(level="ERROR")
            self.tracer.flush()
            return self._failed(routing, f"Extractor failed: {exc}", "extractor", error_details=details)

        # Determine extraction source. The unified pipeline always OCRs with
        # RapidOCR first, so successful extractions are "ocr". "text" is kept
        # for callers that inject raw text directly (unit tests).
        if fields and (ocr_performed or has_text):
            extraction_source = "ocr"
        elif has_text:
            extraction_source = "text"

        tables = getattr(self.extractors[routing.doc_type], "last_tables", [])
        if not isinstance(tables, list):
            tables = []

        # --- 3. Validator (deterministic + hallucination guard) ---
        # RapidOCR text is real OCR output, so the strict evidence check
        # applies (is_image_extraction=False) — stronger than the old relaxed
        # vision path, which improves hallucination control.
        try:
            async with self.timeout_guard.track("validator"):
                validation_errors, completeness, needs_review = self.validator.validate(
                    doc_type=routing.doc_type,
                    fields=fields,
                    document_text=page_text or None,
                    is_image_extraction=False,
                    tables=tables,
                )
            trace.span("validate-fields", output={"errors": validation_errors,
                                                   "completeness": completeness,
                                                   "needs_review": needs_review})
        except Exception as exc:
            trace.span("validate-fields", output={"error": str(exc)}, level="ERROR")
            trace.end(level="ERROR")
            self.tracer.flush()
            return self._failed(
                routing, f"Validator failed: {exc}", "validator",
                fields=fields,
            )

        outcome = register_discovered_fields(self.catalog, routing.doc_type, fields, document_text=page_text)
        fields = outcome.fields
        if outcome.added:
            trace.span("catalog-update", output={"added": outcome.added})

        # --- 4. Judge (text-only; single model, no vision required) ---
        # Skipped only for clean extractions: completeness 100%, no
        # validation errors, every field confident, AND every numeric span
        # verbatim. A confidently-wrong number with a paraphrased span must
        # never skip the only independent sanity check. Saves a full LLM stage.
        judge_result = None
        skip_judge = (
            self.settings.judge_skip_when_clean
            and not validation_errors
            and completeness >= 1.0
            and bool(fields)
            and min([f.confidence for f in fields] + [c.confidence for t in tables for row in t.rows for c in row]) >= self.settings.judge_skip_confidence
            and _all_numeric_spans_verbatim(fields, tables, page_text)
        )
        if skip_judge:
            trace.span("judge-skipped", output={"reason": "clean extraction"})
            logger.info("Judge skipped for %s — clean extraction", filename)
        else:
            judge_gen = trace.generation(
                "judge-extraction",
                model=self.settings.judge_model_name,
                input_data={"doc_type": routing.doc_type,
                            "field_count": len(fields)},
            )
            try:
                async with self.timeout_guard.track("judge"):
                    judge_result = await self.judge.evaluate(
                        fields=fields,
                        source_text=page_text or None,
                        tables=tables,
                    )
                judge_gen.end(
                    output={"score": judge_result.score,
                            "issues": [i.model_dump() if hasattr(i, "model_dump") else i
                                       for i in (judge_result.issues or [])],
                            "notes": judge_result.notes},
                    usage=_agent_usage(self.judge),
                )
                needs_review = needs_review or _judge_demands_review(judge_result)
            except Exception as exc:
                details = _provider_error_details(
                    exc,
                    stage="judge",
                    model=self.settings.judge_model_name,
                    provider=self.settings.llm_provider,
                )
                judge_gen.end(
                    output={"error": str(exc), "provider": details.model_dump() if details else None},
                    level="ERROR",
                    status_message=str(exc)[:500],
                )
                validation_errors.append(f"Judge unavailable: {exc}")
                needs_review = True
                judge_result = None

        # --- PII Detection (log but don't block) ---
        if self.settings.pii_detection_enabled:
            for field in fields:
                if field.value is not None and isinstance(field.value, str):
                    pii_matches = self.pii_detector.check_field_value(field.name, field.value)
                    for match in pii_matches:
                        self.audit_logger.log_pii_detected(
                            client_id=filename,
                            field_name=field.name,
                            pii_type=match.pii_type.value,
                            value_preview=field.value[:50],
                        )

        # --- Auto-eval vs ground truth (when KB has a matching file) ---
        auto_eval = None
        gt = self.knowledge_base.get_ground_truth(Path(filename).stem)
        if gt is not None:
            prediction = {f.name: f.value for f in fields}
            auto_eval = self.evaluate(prediction=prediction, ground_truth=gt, doc_type=routing.doc_type)
            trace.span("auto-eval", output={"f1": auto_eval.f1})
            trace.score("auto-eval-f1", float(auto_eval.f1),
                        comment="ground-truth F1 (KB match)")

        trace.set_io(
            input_data={"filename": filename},
            output_data={
                "doc_type": routing.doc_type,
                "needs_review": needs_review,
                "fields": [
                    {"name": f.name, "value": f.value, "confidence": f.confidence}
                    for f in fields
                ],
            },
        )
        trace.update(metadata={"doc_type": routing.doc_type,
                               "extraction_source": extraction_source,
                               "field_count": len(fields),
                               "judge_skipped": skip_judge})
        trace.score("completeness", float(completeness))
        trace.score("needs_review", 1.0 if needs_review else 0.0)
        if judge_result is not None:
            trace.score("judge-score", float(judge_result.score))

        trace.end()
        self.tracer.flush()

        return ExtractionResult(
            doc_type=routing.doc_type,
            language=routing.language,
            fields=fields,
            tables=tables,
            judge_status="skipped" if skip_judge else "unavailable" if judge_result is None else "flagged" if _judge_demands_review(judge_result) else "passed",
            validation_errors=validation_errors,
            needs_review=needs_review,
            completeness_score=completeness,
            judge=judge_result,
            routing_reason=routing.reason,
            full_text=page_text or None,
            extraction_source=extraction_source,
            auto_evaluation=auto_eval,
        )

    def _failed(
        self,
        routing,
        message: str,
        stage,
        fields: list[ExtractedField] | None = None,
        error_details: ProviderErrorDetails | None = None,
    ) -> ExtractionResult:
        return ExtractionResult(
            doc_type=routing.doc_type,
            language=routing.language,
            routing_reason=routing.reason,
            fields=fields or [],
            validation_errors=[message],
            needs_review=True,
            completeness_score=0.0,
            error=message,
            failed_stage=stage,
            error_details=error_details,
        )

    # ------------------------------------------------------------------
    # Batch jobs
    # ------------------------------------------------------------------

    def create_batch(self) -> BatchCreateResponse:
        job = self.job_store.create()
        return BatchCreateResponse(job_id=job.job_id, status=job.status)

    def get_batch_status(self, job_id):
        job = self.job_store.get(job_id)
        if job is None:
            return None
        result = None
        # Completed and checkpointed partial pages remain readable, including
        # after a later page fails or the worker is interrupted.
        if job.result is not None and job.result.get("documents"):
            try:
                result = FileExtractionResponse.model_validate(job.result)
            except Exception:
                logger.warning("Stored result for job %s failed validation", job_id)
                result = None
        error = None
        if job.status == "failed":
            try:
                error = self.job_store.get_error(job_id)
            except Exception:
                error = None
        return BatchStatusResponse(job_id=job.job_id, status=job.status, result=result, error=error, progress=job.progress)

    async def run_job(self, job_id: UUID, parts: list[UploadedFilePart]) -> None:
        """Background-task entry point: run extraction for an existing job.

        Waits before OCR/stage timers. Failures are persisted; cancellation
        is persisted and re-raised so the background-task owner can clean up.
        """
        queued_at = time.perf_counter()
        logger.info("Job %s queued", job_id)
        token = job_context.set(str(job_id))
        queue_token = queue_wait.set(0)
        try:
            async with self._job_lock:
                self.job_store.mark_processing(job_id)
                logger.info("Job %s processing", job_id)
                queue_wait.set(time.perf_counter() - queued_at)
                await self.extract_group(parts, job_id=job_id)
                job = self.job_store.get(job_id)
                logger.info("Job %s %s", job_id, job.status if job else "unknown")
        except asyncio.CancelledError:
            self.job_store.fail_job(job_id, "Cancelled (server shutdown)")
            logger.info("Job %s cancelled", job_id)
            raise
        except Exception as exc:
            self.job_store.fail_job(job_id, str(exc))
            logger.error(
                "Background extraction %s failed: %s: %.500s",
                job_id, type(exc).__name__, exc,
            )
        finally:
            job_context.reset(token)
            queue_wait.reset(queue_token)


def coerce_field_dates(fields: list[ExtractedField], date_field_names: set[str]) -> list[ExtractedField]:
    """Convert string values for known date fields into datetime.date objects."""
    from app.services.date_formats import KNOWN_DATE_FORMATS

    converted: list[ExtractedField] = []
    for field in fields:
        value = field.value
        if field.name in date_field_names and isinstance(value, str):
            for fmt in KNOWN_DATE_FORMATS:
                try:
                    value = datetime.strptime(value.strip(), fmt).date()
                    break
                except ValueError:
                    continue
        converted.append(field.model_copy(update={"value": value}))
    return converted

