"""Extraction orchestration: upload → OCR/vision → Router → Extractor →
Validator → Judge → (auto-eval). One document per result; multi-page and
multi-document uploads produce one result per page/document."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from app.agents.extractors import build_extractors
from app.agents.judge import JUDGE_PASS_SCORE, JudgeAgent
from app.agents.router import RouterAgent
from app.agents.validator import ValidatorAgent
from app.core.config import Settings
from app.core.security import sanitize_document_text
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
from app.services.job_store import InMemoryJobStore
from app.services.knowledge_base import KnowledgeBaseRepository
from app.services.llamaparse_client import LlamaParseClient

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


class UploadedFilePart:
    """One raw uploaded file, before it's been split into pages."""

    def __init__(self, filename: str, content_type: str | None, raw_content: bytes) -> None:
        self.filename = filename
        self.content_type = content_type
        self.raw_content = raw_content


class DocumentExtractionService:
    def __init__(self, settings: Settings, job_store: InMemoryJobStore | None = None) -> None:
        self.settings = settings
        self.knowledge_base = KnowledgeBaseRepository(Path(settings.knowledge_base_path))
        self.catalog = self.knowledge_base.catalog
        self.llamaparse = LlamaParseClient(settings.llama_cloud_api_key)
        self.tracer = LangfuseTracer(settings)
        self.router = RouterAgent(settings)
        self.extractors = build_extractors(settings, self.catalog)
        self.validator = ValidatorAgent(self.catalog)
        self.judge = JudgeAgent(settings)
        self.job_store = job_store or InMemoryJobStore()

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

    async def extract_group(self, parts: list[UploadedFilePart]) -> FileExtractionResponse:
        total_size = sum(len(p.raw_content) for p in parts)
        combined_name = parts[0].filename if len(parts) == 1 else f"{len(parts)} files ({parts[0].filename}, ...)"
        request_meta = FileUploadMeta(
            filename=combined_name,
            content_type=parts[0].content_type if len(parts) == 1 else None,
            size_bytes=total_size,
        )

        # Per-file routing: images → direct vision (OCR/ICR via VL model),
        # everything else → LlamaParse (one result per parsed page).
        image_parts = [(i, p) for i, p in enumerate(parts) if _is_image_file(p.filename, p.content_type)]
        doc_parts = [(i, p) for i, p in enumerate(parts) if not _is_image_file(p.filename, p.content_type)]
        results_by_index: dict[int, list[ExtractionResult]] = {}

        if image_parts:
            vision_docs = await asyncio.gather(*[
                self._extract_one_page(
                    filename=combined_name,
                    page_text="",
                    image_bytes=part.raw_content,
                    image_media_type=_image_media_type(part.filename, part.content_type),
                )
                for _, part in image_parts
            ])
            for (idx, _), document in zip(image_parts, vision_docs):
                results_by_index[idx] = [document]

        if doc_parts:
            try:
                parse_results = await asyncio.gather(*[
                    self.llamaparse.aparse_file(part.raw_content, part.filename)
                    for _, part in doc_parts
                ])
            except Exception as exc:
                return FileExtractionResponse(request=request_meta, error=f"Failed to parse uploaded file(s): {exc}")

            pages_with_index = [
                (idx, page_text)
                for (idx, _), parsed_pages in zip(doc_parts, parse_results)
                for page_text in parsed_pages
            ]

            text_docs = await asyncio.gather(*[
                self._extract_one_page(filename=combined_name, page_text=page_text)
                for _, page_text in pages_with_index
            ])
            for (idx, _), document in zip(pages_with_index, text_docs):
                results_by_index.setdefault(idx, []).append(document)

        documents = [doc for i in range(len(parts)) for doc in results_by_index.get(i, [])]
        if not documents:
            return FileExtractionResponse(
                request=request_meta,
                error="No readable pages/text found in the uploaded file(s).",
            )
        return FileExtractionResponse(request=request_meta, documents=documents)

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
            {"filename": filename, "has_image": bool(image_bytes), "text_chars": len(page_text or "")},
        )

        # --- 1. Router ---
        try:
            routing = await self.router.classify(
                filename=filename,
                text_hint=sanitize_document_text(page_text) or None,
                image_bytes=image_bytes,
                image_media_type=image_media_type,
            )
            trace.span("router", output={"doc_type": routing.doc_type, "confidence": routing.confidence})
        except Exception as exc:
            trace.span("router", output={"error": str(exc)})
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

        try:
            fields, new_field_names = await self.extractors[routing.doc_type].extract(
                text=page_text or "",
                image_bytes=image_bytes,
                image_media_type=image_media_type,
                few_shot=few_shot,
            )
            trace.span("extractor", output={"fields": len(fields), "new_fields": new_field_names})
        except Exception as exc:
            trace.span("extractor", output={"error": str(exc)})
            self.tracer.flush()
            return self._failed(routing, f"Extractor failed: {exc}", "extractor")

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
        try:
            validation_errors, completeness, needs_review = self.validator.validate(
                doc_type=routing.doc_type,
                fields=fields,
                document_text=page_text or None,
            )
            trace.span("validator", output={"errors": validation_errors, "completeness": completeness})
        except Exception as exc:
            trace.span("validator", output={"error": str(exc)})
            self.tracer.flush()
            return self._failed(
                routing, f"Validator failed: {exc}", "validator",
                fields=fields,
            )

        # --- 4. Judge ---
        try:
            judge_result = await self.judge.evaluate(
                fields=fields,
                source_text=page_text or None,
                image_bytes=image_bytes,
                image_media_type=image_media_type,
            )
            trace.span("judge", output={"score": judge_result.score})
            needs_review = needs_review or judge_result.score < JUDGE_PASS_SCORE
        except Exception as exc:
            trace.span("judge", output={"error": str(exc)})
            validation_errors.append(f"Judge unavailable: {exc}")
            needs_review = True
            judge_result = None

        # --- Auto-eval vs ground truth (when KB has a matching file) ---
        auto_eval = None
        gt = self.knowledge_base.get_ground_truth(Path(filename).stem)
        if gt is not None:
            prediction = {f.name: f.value for f in fields}
            auto_eval = self.evaluate(prediction=prediction, ground_truth=gt, doc_type=routing.doc_type)
            trace.span("auto-eval", output={"f1": auto_eval.f1})

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
        if job.result is not None:
            result = FileExtractionResponse.model_validate(job.result)
        return BatchStatusResponse(job_id=job.job_id, status=job.status, result=result)


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

