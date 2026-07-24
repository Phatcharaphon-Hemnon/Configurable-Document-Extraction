from __future__ import annotations

import json
import logging

from app.core.config import Settings
from app.schemas.documents import JudgeIssue, JudgeResult
from app.schemas.llm_schemas import JudgeResponseSchema
from app.services.sut_genai_client import SutGenAICallError as GeminiCallError, SutGenAIClient as GeminiClient

logger = logging.getLogger(__name__)



class JudgeAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = GeminiClient(settings)

    async def evaluate(
        self,
        prediction: dict[str, object],
        source_text: str | None = None,
        image_bytes: bytes | None = None,
        image_media_type: str | None = None,
    ) -> JudgeResult:
        has_text = bool(source_text and source_text.strip())
        has_image = bool(image_bytes)
        if not has_text and not has_image:
            raise GeminiCallError("Judge requires source text or an image")

        prompt_parts = [
            "You are a strict document-extraction judge.",
            "Compare the predicted extracted fields against the original document.",
            "Penalize hallucinated, unsupported, or incorrect values.",
            "Flag fields whose values cannot be verified in the source document.",
            f"Predicted fields: {json.dumps(prediction, ensure_ascii=False, indent=2)}",
        ]
        if has_text:
            prompt_parts.append(f"Source text:\n{source_text.strip()}")
        if has_image:
            prompt_parts.append("The original document image is attached. Verify extracted values against it.")

        prompt = "\n\n".join(prompt_parts)

        if has_image and image_bytes and image_media_type:
            result = await self._client.generate_structured_with_image(
                model=self.settings.judge_model_name,
                prompt=prompt,
                image_bytes=image_bytes,
                image_media_type=image_media_type,
                response_schema=JudgeResponseSchema,
            )
        else:
            result = await self._client.generate_structured(
                model=self.settings.judge_model_name,
                prompt=prompt,
                response_schema=JudgeResponseSchema,
            )
        logger.info(
            "Judge tokens: prompt=%s completion=%s total=%s",
            result.prompt_tokens,
            result.completion_tokens,
            result.total_tokens,
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