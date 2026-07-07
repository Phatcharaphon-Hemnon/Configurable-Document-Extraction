from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from Backend.app.core.config import Settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class GeminiCallError(Exception):
    """Raised when a Gemini API call fails."""

    def __init__(self, message: str, *, request_summary: dict[str, Any] | None = None, raw_response: Any = None) -> None:
        super().__init__(message)
        self.request_summary = request_summary or {}
        self.raw_response = raw_response


@dataclass(slots=True)
class GeminiCallResult:
    parsed: BaseModel | None
    raw_text: str | None
    raw_response: Any
    request_summary: dict[str, Any]


class GeminiClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        api_key = settings.gemini_api_key.strip()
        if not api_key:
            raise GeminiCallError("GEMINI_API_KEY is not set")
        self._client = genai.Client(api_key=api_key)

    @staticmethod
    def _truncate(value: str, limit: int = 500) -> str:
        if len(value) <= limit:
            return value
        return f"{value[:limit]}… [truncated, {len(value)} chars total]"

    @staticmethod
    def _summarize_image(image_bytes: bytes | None, mime_type: str | None) -> dict[str, Any] | None:
        if not image_bytes:
            return None
        return {
            "mime_type": mime_type or "application/octet-stream",
            "size_bytes": len(image_bytes),
            "base64_preview": f"<{len(image_bytes)} bytes, omitted>",
        }

    def generate_structured(
        self,
        *,
        model: str,
        prompt: str,
        response_schema: type[T],
        image_bytes: bytes | None = None,
        image_mime_type: str | None = None,
        temperature: float = 0.0,
    ) -> GeminiCallResult:
        parts: list[types.Part] = [types.Part.from_text(text=prompt)]
        if image_bytes is not None:
            parts.append(
                types.Part.from_bytes(
                    data=image_bytes,
                    mime_type=image_mime_type or "image/png",
                )
            )

        config = types.GenerateContentConfig(
            temperature=temperature,
            response_mime_type="application/json",
            response_schema=response_schema,
        )

        # Use the model name directly without 'models/' prefix if it's already there, 
        # or ensure it's in the format the SDK expects.
        target_model = model
        if not target_model.startswith("models/"):
            target_model = f"models/{target_model}"

        request_summary = {
            "model": target_model,
            "prompt": self._truncate(prompt),
            "image": self._summarize_image(image_bytes, image_mime_type),
            "response_schema": response_schema.__name__,
            "temperature": temperature,
        }

        try:
            response = self._client.models.generate_content(
                model=target_model,
                contents=parts,
                config=config,
            )
        except Exception as exc:
            raise GeminiCallError(
                f"Gemini API call failed: {exc}",
                request_summary=request_summary,
            ) from exc

        raw_text = getattr(response, "text", None)

        # TEMP DEBUG — remove after bug is found
        # We want to see what Gemini returned before/after schema parsing.
        raw_json_preview: str | None = None
        if isinstance(raw_text, str) and raw_text.strip():
            raw_json_preview = raw_text[:200]
            logger.debug(
                "TEMP DEBUG Gemini generate_structured raw JSON preview: %s",
                raw_json_preview,
            )
        else:
            logger.debug("TEMP DEBUG Gemini generate_structured raw JSON preview: <empty>")

        parsed = getattr(response, "parsed", None)

        if parsed is None and raw_text:
            try:
                parsed = response_schema.model_validate_json(raw_text)
            except Exception as exc:
                raise GeminiCallError(
                    f"Failed to parse Gemini JSON response: {exc}",
                    request_summary=request_summary,
                    raw_response=raw_text,
                ) from exc

        # TEMP DEBUG — remove after bug is found
        parsed_field_count: int | None = None
        try:
            # We specifically care about ExtractionResponseSchema (fields array)
            # but this logging is safe for other schemas too.
            if hasattr(parsed, "fields") and isinstance(getattr(parsed, "fields"), list):
                parsed_field_count = len(getattr(parsed, "fields"))
        except Exception:
            parsed_field_count = None

        logger.debug(
            "TEMP DEBUG Gemini generate_structured summary=%s parsed_field_count=%s",
            {
                "model": request_summary.get("model"),
                "prompt_len": len(prompt),
                "response_schema": request_summary.get("response_schema"),
                "image_mime_type": request_summary.get("image", {}).get("mime_type") if isinstance(request_summary.get("image"), dict) else None,
            },
            parsed_field_count,
        )

        if parsed is None:
            raise GeminiCallError(
                "Gemini returned an empty response",
                request_summary=request_summary,
                raw_response=_safe_serialize(response),
            )

        return GeminiCallResult(
            parsed=parsed,
            raw_text=raw_text,
            raw_response=_safe_serialize(response),
            request_summary=request_summary,
        )

    def generate_text(
        self,
        *,
        model: str,
        prompt: str,
        image_bytes: bytes | None = None,
        image_mime_type: str | None = None,
        temperature: float = 0.0,
    ) -> GeminiCallResult:
        parts: list[types.Part] = [types.Part.from_text(text=prompt)]
        if image_bytes is not None:
            parts.append(
                types.Part.from_bytes(
                    data=image_bytes,
                    mime_type=image_mime_type or "image/png",
                )
            )

        config = types.GenerateContentConfig(temperature=temperature)
        request_summary = {
            "model": model,
            "prompt": self._truncate(prompt),
            "image": self._summarize_image(image_bytes, image_mime_type),
            "temperature": temperature,
        }

        try:
            response = self._client.models.generate_content(
                model=model,
                contents=parts,
                config=config,
            )
        except Exception as exc:
            raise GeminiCallError(
                f"Gemini API call failed: {exc}",
                request_summary=request_summary,
            ) from exc

        raw_text = getattr(response, "text", None)
        if not raw_text or not raw_text.strip():
            raise GeminiCallError(
                "Gemini returned an empty text response",
                request_summary=request_summary,
                raw_response=_safe_serialize(response),
            )

        return GeminiCallResult(
            parsed=None,
            raw_text=raw_text.strip(),
            raw_response=_safe_serialize(response),
            request_summary=request_summary,
        )


def _safe_serialize(response: Any) -> Any:
    for attr in ("model_dump", "to_dict", "dict"):
        fn = getattr(response, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass
    try:
        return json.loads(json.dumps(response, default=str))
    except Exception:
        return str(response)
