from pathlib import Path
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
        for field_name, expected_value in ground_truth.items():
            if prediction.get(field_name) == expected_value:
                matched_fields += 1
        total_fields = max(len(ground_truth), 1)
        score = matched_fields / total_fields
        mismatches = [
            {"field": field_name, "predicted": prediction.get(field_name), "expected": expected_value}
            for field_name, expected_value in ground_truth.items()
            if prediction.get(field_name) != expected_value
        ]
        return EvaluateResponse(score=score, summary="Evaluation completed", mismatches=mismatches)

    def extract(self, filename: str, content_type: str | None, text: str) -> ExtractionResult:
        routing = self.router.classify(filename=filename, content_type=content_type)
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
            extracted_fields=extracted_fields,
            validation=validation,
            judge=judge_result,
        )

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
