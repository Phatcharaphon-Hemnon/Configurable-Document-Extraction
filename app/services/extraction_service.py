from pathlib import Path
import base64
import json
from urllib import error, request
from typing import Any

from app.agents.extractors import ExtractionContext, ExtractorFactory
from app.agents.judge import JudgeAgent
from app.agents.router import RouterAgent
from app.agents.validator import ValidatorAgent
from app.core.config import Settings
from app.schemas.documents import BatchCreateResponse, BatchStatusResponse, EvaluateResponse, ExtractionResult, FileUploadMeta
from app.services.job_store import InMemoryJobStore
from app.services.knowledge_base import KnowledgeBaseRepository


class DocumentExtractionService:
    def __init__(self, settings: Settings, job_store: InMemoryJobStore | None = None) -> None:
        self.settings = settings
        self.router = RouterAgent(settings)
        self.extractor_factory = ExtractorFactory()
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
        if ocr_text is not None and ocr_text.strip():
            text = ocr_text.strip()

        image_text: str | None = None
        if not text.strip() and raw_content is not None and self._is_image_upload(filename=filename, content_type=content_type):
            image_text = self._extract_text_from_image(raw_content=raw_content, content_type=content_type)
            if image_text:
                text = image_text

        routing = self.router.classify(filename=filename, content_type=content_type, text_hint=image_text or text)
        extractor = self.extractor_factory.create(routing.doc_type)
        context = ExtractionContext(text=text, metadata={"filename": filename, "content_type": content_type})
        extracted_fields = extractor.extract(context)
        validation = self.validator.validate(routing.doc_type, extracted_fields)
        judge_result = self.judge.evaluate(
            prediction={field_name: field.value for field_name, field in extracted_fields.items()},
            source_text=text,
        )

        return ExtractionResult(
            request=FileUploadMeta(filename=filename, content_type=content_type, size_bytes=len(text.encode("utf-8"))),
            doc_type=routing.doc_type,
            language=routing.language,
            routing_reason=routing.reason,
            extracted_fields=extracted_fields,
            validation=validation,
            judge=judge_result,
        )

    def _is_image_upload(self, filename: str, content_type: str | None) -> bool:
        lower_name = filename.lower()
        if content_type is not None and content_type.startswith("image/"):
            return True
        return lower_name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"))

    def _extract_text_from_image(self, raw_content: bytes, content_type: str | None) -> str | None:
        api_key = self.settings.gemini_api_key.strip()
        model_name = self.settings.recommended_extraction_model_name.strip()

        if not api_key or not model_name.startswith("gemini"):
            return None

        mime_type = content_type or "image/png"
        encoded_image = base64.b64encode(raw_content).decode("utf-8")
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                "Extract all readable text from this document image. "
                                "Return only the text content, preserve line breaks, and do not add commentary."
                            ),
                        },
                        {
                            "inlineData": {
                                "mimeType": mime_type,
                                "data": encoded_image,
                            }
                        },
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.0,
            },
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        request_object = request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(request_object, timeout=20) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except (error.HTTPError, error.URLError, TimeoutError, ValueError):
            return None

        candidates = response_payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            return None

        first_candidate = candidates[0]
        if not isinstance(first_candidate, dict):
            return None

        content = first_candidate.get("content")
        if not isinstance(content, dict):
            return None

        parts = content.get("parts")
        if not isinstance(parts, list):
            return None

        extracted_text: list[str] = []
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                extracted_text.append(part["text"])

        joined_text = "".join(extracted_text).strip()
        return joined_text or None

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
