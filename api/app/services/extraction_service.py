"""Extraction orchestration: upload → RapidOCR → Router → Extractor →
Validator → Judge → (auto-eval). One document per result; multi-page and
multi-document uploads produce one result per page/document."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from app.agents.extractors import build_extractors
from app.agents.judge import JUDGE_PASS_SCORE, JudgeAgent
from app.agents.router import RouterAgent
from app.agents.validator import ValidatorAgent
from app.core.config import Settings
from app.core.security import sanitize_document_text
from app.guards.audit_logger import AuditLogger
from app.guards.content_guard import ContentLimits, validate_document_text
from app.guards.pii_detector import PIIDetector
from app.guards.timeout_guard import StageTimeoutConfig, TimeoutGuard
from app.observability.langfuse import LangfuseTracer
from app.schemas.documents import (
    BatchCreateResponse,
    BatchStatusResponse,
    EvaluateResponse,
    ExtractedField,
    ExtractionResult,
    FileExtractionResponse,
    FileUploadMeta,
)
from app.services.field_catalog import is_registerable_new_field, normalize_field_name
from app.services.field_matching import values_match
from app.services.job_store import InMemoryJobStore, JobRecord, SQLiteJobStore
from app.services.knowledge_base import KnowledgeBaseRepository
from app.services.rapidocr_client import RapidOCRClient
from app.services.request_control import job_context

logger = logging.getLogger(__name__)

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
        self.ocr = RapidOCRClient(
            dpi=getattr(settings, "ocr_dpi", 300),
            enable_cache=getattr(settings, "ocr_cache_enabled", True),
        )
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

        try:
            # Unified local-OCR path (RapidOCR): every upload — image or PDF —
            # is OCR'd locally into per-page text, then flows through the
            # text-only pipeline (Router → Extractor → Validator → Judge)
            # using the single configured text model. No vision model and no
            # cloud OCR calls are required.
            try:
                parse_results = await asyncio.gather(*[
                    self.ocr.aparse_file(part.raw_content, part.filename)
                    for part in parts
                ])
            except Exception as exc:
                self.job_store.save_result(job.job_id, {
                    "documents": [],
                    "error": f"Failed to OCR uploaded file(s): {exc}",
                })
                return FileExtractionResponse(
                    request=request_meta,
                    error=f"Failed to OCR uploaded file(s): {exc}",
                    job_id=job_id_str,
                )

            pages_with_index = [
                (idx, page_text)
                for idx, parsed_pages in enumerate(parse_results)
                for page_text in parsed_pages
            ]

            text_docs = [
                await self._extract_one_page(filename=combined_name, page_text=page_text)
                for _, page_text in pages_with_index
            ]
            results_by_index: dict[int, list[ExtractionResult]] = {}
            for (idx, _), document in zip(pages_with_index, text_docs):
                results_by_index.setdefault(idx, []).append(document)

            documents = [doc for i in range(len(parts)) for doc in results_by_index.get(i, [])]
            if not documents:
                self.job_store.save_result(job.job_id, {
                    "documents": [],
                    "error": "No readable pages/text found in the uploaded file(s).",
                })
                return FileExtractionResponse(
                    request=request_meta,
                    error="No readable pages/text found in the uploaded file(s).",
                    job_id=job_id_str,
                )

            # Save successful results to database
            result_dict = {
                "documents": [doc.model_dump() for doc in documents],
            }
            self.job_store.save_result(job.job_id, result_dict)

            return FileExtractionResponse(
                request=request_meta,
                documents=documents,
                job_id=job_id_str,
            )
        except asyncio.CancelledError:
            # Client disconnected / server shutting down: record the job as
            # failed instead of orphaning it as 'queued', then re-raise.
            try:
                self.job_store.fail_job(job.job_id, "Cancelled (client disconnected or server shutdown)")
            except Exception:
                logger.warning("Could not mark job %s as cancelled", job.job_id)
            raise
        except Exception as exc:
            # Save error to database
            self.job_store.save_result(job.job_id, {
                "documents": [],
                "error": str(exc),
            })
            raise

    # ------------------------------------------------------------------
    # Single page pipeline
    # ------------------------------------------------------------------

    async def _extract_one_page(
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
            router_gen.end(output={"error": str(exc)}, level="ERROR", status_message=str(exc)[:500])
            trace.end(level="ERROR")
            self.tracer.flush()
            return ExtractionResult(
                doc_type="invoice",  # placeholder; overwritten below by schema-safe error path
                fields=[],
                validation_errors=[f"Router failed: {exc}"],
                needs_review=True,
                error=f"Router failed: {exc}",
                failed_stage="router",
            )

        # --- 2. Extractor (per doc type) ---
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
            if not fields:
                raise ValueError("No usable fields were extracted from the OCR text. "
                                 "Check the OCR text or retry extraction with a suitable text model.")
            extract_gen.end(
                output={"fields": [{"name": f.name, "confidence": f.confidence} for f in fields],
                        "new_fields": new_field_names},
                usage=_agent_usage(self.extractors[routing.doc_type]),
            )
        except Exception as exc:
            extract_gen.end(output={"error": str(exc)}, level="ERROR", status_message=str(exc)[:500])
            trace.end(level="ERROR")
            self.tracer.flush()
            return self._failed(routing, f"Extractor failed: {exc}", "extractor")

        # Determine extraction source. The unified pipeline always OCRs with
        # RapidOCR first, so successful extractions are "ocr". "text" is kept
        # for callers that inject raw text directly (unit tests).
        if fields and (ocr_performed or has_text):
            extraction_source = "ocr"
        elif has_text:
            extraction_source = "text"

        # Register AI-discovered field names into the catalog (project rule).
        # The service is the source of truth for is_new_field AND for what is
        # worth registering: only REAL values (non-placeholder), sane snake_case
        # names, confidence >= threshold. 'N/A' junk never reaches the catalog.
        known_before = self.catalog.known_names(routing.doc_type)
        fields = [
            f.model_copy(update={"is_new_field": normalize_field_name(f.name) not in known_before})
            for f in fields
        ]
        new_field_names = [
            f.name
            for f in fields
            if f.is_new_field and is_registerable_new_field(f.name, f.value, f.confidence)
        ]
        if new_field_names:
            added = self.catalog.add_fields(routing.doc_type, new_field_names)
            if added:
                logger.info("Catalog updated for %s: +%s", routing.doc_type, added)
                trace.span("catalog-update", output={"added": added})

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

        # --- 4. Judge (text-only; single model, no vision required) ---
        # Skipped entirely for clean extractions: completeness 100%, no
        # validation errors, every field confident. Saves a full LLM stage.
        judge_result = None
        skip_judge = (
            self.settings.judge_skip_when_clean
            and not validation_errors
            and completeness >= 1.0
            and bool(fields)
            and min(f.confidence for f in fields) >= self.settings.judge_skip_confidence
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
                    )
                judge_gen.end(
                    output={"score": judge_result.score,
                            "issues": [i.model_dump() if hasattr(i, "model_dump") else i
                                       for i in (judge_result.issues or [])],
                            "notes": judge_result.notes},
                    usage=_agent_usage(self.judge),
                )
                needs_review = needs_review or judge_result.score < JUDGE_PASS_SCORE
            except Exception as exc:
                judge_gen.end(output={"error": str(exc)}, level="ERROR",
                              status_message=str(exc)[:500])
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
    ) -> ExtractionResult:
        return ExtractionResult(
            doc_type=routing.doc_type,
            language=routing.language,
            routing_reason=routing.reason,
            fields=fields or [],
            validation_errors=[message],
            needs_review=True,
            error=message,
            failed_stage=stage,
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
        # Only completed jobs carry a valid FileExtractionResponse; failed
        # rows store just an error message (surfaced via `error` below).
        if job.status == "completed" and job.result is not None:
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
        return BatchStatusResponse(job_id=job.job_id, status=job.status, result=result, error=error)

    async def run_job(self, job_id: UUID, parts: list[UploadedFilePart]) -> None:
        """Background-task entry point: run extraction for an existing job.

        Waits before OCR/stage timers. Failures are persisted; cancellation
        is persisted and re-raised so the background-task owner can clean up.
        """
        logger.info("Job %s queued", job_id)
        token = job_context.set(str(job_id))
        try:
            async with self._job_lock:
                self.job_store.mark_processing(job_id)
                logger.info("Job %s processing", job_id)
                await self.extract_group(parts, job_id=job_id)
                job = self.job_store.get(job_id)
                logger.info("Job %s %s", job_id, job.status if job else "unknown")
        except asyncio.CancelledError:
            self.job_store.fail_job(job_id, "Cancelled (server shutdown)")
            logger.info("Job %s cancelled", job_id)
            raise
        except Exception as exc:
            self.job_store.fail_job(job_id, str(exc))
            logger.error("Background extraction %s failed: %s", job_id, type(exc).__name__)
        finally:
            job_context.reset(token)


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

