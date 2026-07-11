from __future__ import annotations

import json

from Backend.app.core.config import Settings
from Backend.app.schemas.documents import JudgeIssue, JudgeResult
from Backend.app.schemas.gemini_schemas import JudgeResponseSchema
from Backend.app.services.sut_genai_client import SutGenAICallError as GeminiCallError, SutGenAIClient as GeminiClient


class JudgeAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = GeminiClient(settings)

    def evaluate(
        self,
        prediction: dict[str, object],
        source_text: str | None = None,
        image_bytes: bytes | None = None,
        image_mime_type: str | None = None,
    ) -> JudgeResult:
        if not source_text and image_bytes is None:
            raise GeminiCallError("Judge requires source text and/or the original document image")

        prompt_parts = [
            "You are a strict document-extraction judge.",
            "Compare the predicted extracted fields against the original document.",
            "Penalize hallucinated, unsupported, or incorrect values.",
            "Flag fields whose values cannot be verified in the source document.",
            f"Predicted fields: {json.dumps(prediction, ensure_ascii=False, indent=2)}",
        ]
        if source_text and source_text.strip():
            prompt_parts.append(f"Source text (OCR/Hint):\n{source_text.strip()}")
        if image_bytes is not None:
            prompt_parts.append(
                "The original document image is attached. Cross-check every predicted field against what is visible. "
                "The image is the ground truth."
            )

        prompt = "\n\n".join(prompt_parts)

        result = self._client.generate_structured(
            model=self.settings.judge_model_name,
            prompt=prompt,
            response_schema=JudgeResponseSchema,
            image_bytes=image_bytes,
            image_mime_type=image_mime_type,
        )

        parsed = result.parsed
        assert isinstance(parsed, JudgeResponseSchema)

        judge_issues = [
            JudgeIssue(field=issue.field, message=issue.message, severity=issue.severity)
            for issue in parsed.issues
        ]

        return JudgeResult(
            score=parsed.score,
            issues=judge_issues,
            notes=parsed.notes,
        )
