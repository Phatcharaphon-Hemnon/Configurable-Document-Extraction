from __future__ import annotations

from pathlib import Path
from typing import Any

from Backend.app.agents.extractors import ExtractionContext, ExtractorFactory
from Backend.app.agents.judge import JudgeAgent
from Backend.app.agents.router import RouterAgent
from Backend.app.agents.validator import ValidatorAgent
from Backend.app.core.config import Settings
from Backend.app.schemas.documents import (
    BatchCreateResponse,
    BatchStatusResponse,
    EvaluateResponse,
    ExtractionResult,
    FileUploadMeta,
    ValidationResult,
)
from Backend.app.services.gemini_client import GeminiCallError, GeminiClient
from Backend.app.services.job_store import InMemoryJobStore
from Backend.app.services.knowledge_base import KnowledgeBaseRepository


class DocumentExtractionService:
    def __init__(self, settings: Settings, job_store: InMemoryJobStore | None = None) -> None:
        self.settings = settings
        self.router = RouterAgent(settings)
        self.extractor_factory = ExtractorFactory(settings)
        self.validator = ValidatorAgent()
        self.judge = JudgeAgent(settings)
        self.job_store = job_store or InMemoryJobStore()
        self.knowledge_base = KnowledgeBaseRepository(Path(settings.knowledge_base_path))

    def list_templates(self) -> list[Any]:
        return self.knowledge_base.list_templates()

    def evaluate(self, prediction: dict[str, Any], ground_truth: dict[str, Any], source_text: str | None = None) -> EvaluateResponse:
        matched_fields = 0
        false_positives = 0
        false_negatives = 0
        mismatches = [
            {"field": field_name, "predicted": prediction.get(field_name), "expected": expected_value}
            for field_name, expected_value in ground_truth.items()
            if prediction.get(field_name) != expected_value
        ]

        for field_name, expected_value in ground_truth.items():
            if prediction.get(field_name) == expected_value:
                matched_fields += 1
            else:
                false_negatives += 1

        for field_name, predicted_value in prediction.items():
            if ground_truth.get(field_name) != predicted_value:
                false_positives += 1

        precision = matched_fields / (matched_fields + false_positives) if (matched_fields + false_positives) else 1.0
        recall = matched_fields / (matched_fields + false_negatives) if (matched_fields + false_negatives) else 1.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

        return EvaluateResponse(
            score=f1,
            precision=precision,
            recall=recall,
            f1=f1,
            summary="Evaluation completed",
            mismatches=mismatches,
        )

    def extract(
        self,
        filename: str,
        content_type: str | None,
        text: str,
        raw_content: bytes | None = None,
        ocr_text: str | None = None,
    ) -> ExtractionResult:
        request_meta = FileUploadMeta(
            filename=filename,
            content_type=content_type,
            size_bytes=len(raw_content) if raw_content is not None else None,
        )

        is_image = raw_content is not None and self._is_image_upload(filename=filename, content_type=content_type)
        image_bytes = raw_content if is_image else None
        image_mime_type = content_type if is_image else None

        final_text = ""
        if ocr_text is not None and ocr_text.strip():
            final_text = ocr_text.strip()
        elif text is not None and text.strip():
            final_text = text.strip()
        elif is_image and image_bytes is not None:
            try:
                final_text = self._extract_text_from_image(image_bytes=image_bytes, mime_type=image_mime_type) or ""
            except GeminiCallError as exc:
                # TEMP DEBUG — remove after bug is found
                print(f"[TEMP DEBUG] _extract_text_from_image GeminiCallError: {exc}")
                final_text = ""

        try:
            routing = self.router.classify(
                filename=filename,
                content_type=content_type,
                text_hint=final_text or None,
                image_bytes=image_bytes,
                image_mime_type=image_mime_type,
            )

            extractor = self.extractor_factory.create(routing.doc_type)
            context = ExtractionContext(
                text=final_text,
                metadata={"filename": filename, "content_type": content_type},
                image_bytes=image_bytes,
                image_mime_type=image_mime_type,
            )
            extracted_fields = extractor.extract(context)

            validation = self.validator.validate(routing.doc_type, extracted_fields)
            judge_result = self.judge.evaluate(
                prediction={field_name: field.value for field_name, field in extracted_fields.items()},
                source_text=final_text or None,
                image_bytes=image_bytes,
                image_mime_type=image_mime_type,
            )

            return ExtractionResult(
                request=request_meta,
                doc_type=routing.doc_type,
                language=routing.language,
                routing_reason=routing.reason,
                extracted_fields=extracted_fields,
                full_text=final_text or None,
                validation=validation,
                judge=judge_result,
                needs_review=not validation.is_valid or (judge_result.score < 0.7),
            )
        except GeminiCallError as exc:
            return ExtractionResult(
                request=request_meta,
                needs_review=True,
                error=str(exc),
                validation=ValidationResult(is_valid=False, issues=[]),
            )
        except Exception as exc:
            return ExtractionResult(
                request=request_meta,
                needs_review=True,
                error=f"Extraction pipeline failed: {exc}",
                validation=ValidationResult(is_valid=False, issues=[]),
            )

    def _is_image_upload(self, filename: str, content_type: str | None) -> bool:
        lower_name = filename.lower()
        if content_type is not None and content_type.startswith("image/"):
            return True
        return lower_name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"))

    def _extract_text_from_image(self, image_bytes: bytes, mime_type: str | None) -> str | None:
        client = GeminiClient(self.settings)
        result = client.generate_text(
            model=self.settings.recommended_extraction_model_name,
            prompt=(
                "Extract all readable text from this document image. "
                "Return only the text content, preserve line breaks, and do not add commentary."
            ),
            image_bytes=image_bytes,
            image_mime_type=mime_type,
        )
        return result.raw_text

    def create_batch(self) -> BatchCreateResponse:
        job = self.job_store.create()
        return BatchCreateResponse(job_id=job.job_id, status=job.status)

    def get_batch_status(self, job_id):
        job = self.job_store.get(job_id)
        if job is None:
            return None
        result = None
        if job.result is not None:
            result = ExtractionResult.model_validate(job.result)
        return BatchStatusResponse(job_id=job.job_id, status=job.status, result=result)
