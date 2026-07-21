from __future__ import annotations

import json

from app.core.config import Settings
from app.schemas.documents import JudgeIssue, JudgeResult
from app.schemas.llm_schemas import JudgeResponseSchema
from app.services.sut_genai_client import SutGenAICallError as GeminiCallError, SutGenAIClient as GeminiClient


class JudgeAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = GeminiClient(settings)

    async def evaluate(
        self,
        prediction: dict[str, object],
        source_text: str | None = None,
    ) -> JudgeResult:
        if not source_text or not source_text.strip():
            raise GeminiCallError("Judge requires source text")

        prompt_parts = [
            "You are a strict document-extraction judge.",
            "Compare the predicted extracted fields against the original document.",
            "Penalize hallucinated, unsupported, or incorrect values.",
            "Flag fields whose values cannot be verified in the source document.",
            f"Predicted fields: {json.dumps(prediction, ensure_ascii=False, indent=2)}",
            f"Source text:\n{source_text.strip()}",
        ]

        prompt = "\n\n".join(prompt_parts)

        result = await self._client.generate_structured(
            model=self.settings.judge_model_name,
            prompt=prompt,
            response_schema=JudgeResponseSchema,
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