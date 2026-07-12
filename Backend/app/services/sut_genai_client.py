"""SUT GenAI gateway client.

Talks to https://genai.sut.ac.th/api which is an Open WebUI instance that
exposes an OpenAI-compatible chat-completions endpoint and proxies to
OpenRouter-catalog models underneath.

Auth:  Authorization: Bearer <SUT_GENAI_API_KEY>
Model: any slug from the SUT catalog, e.g. "anthropic/claude-haiku-4.5"

Structured output strategy
---------------------------
The proxy advertises ``response_format`` / ``structured_outputs`` in its
supported_parameters list.  We try the OpenAI-style JSON-schema approach first:

    response_format={
        "type": "json_schema",
        "json_schema": {
            "name": "<SchemaName>",
            "strict": True,
            "schema": <JSON-Schema-dict>,
        },
    }

If the proxy ignores / rejects the schema param we fall back to explicit
prompt-level JSON instructions + manual Pydantic parse.  The fallback is
transparent to callers — they always get a ``SutGenAICallResult``.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from typing import Any, TypeVar

from openai import OpenAI
from pydantic import BaseModel

from app.core.config import Settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_SUT_BASE_URL = "https://genai.sut.ac.th/api"


# ---------------------------------------------------------------------------
# Public exception / result types (mirror the old GeminiCallError interface
# so all existing call sites keep working with a simple import swap).
# ---------------------------------------------------------------------------

class SutGenAICallError(Exception):
    """Raised when a SUT GenAI API call fails."""

    def __init__(
        self,
        message: str,
        *,
        request_summary: dict[str, Any] | None = None,
        raw_response: Any = None,
    ) -> None:
        super().__init__(message)
        self.request_summary = request_summary or {}
        self.raw_response = raw_response


# Keep the old name as an alias so any code that still imports GeminiCallError
# from this module doesn't break during the transition.
GeminiCallError = SutGenAICallError


@dataclass(slots=True)
class SutGenAICallResult:
    parsed: BaseModel | None
    raw_text: str | None
    raw_response: Any
    request_summary: dict[str, Any]


# Keep old name as alias.
GeminiCallResult = SutGenAICallResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _truncate(value: str, limit: int = 500) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}… [truncated, {len(value)} chars total]"


def _image_to_data_url(image_bytes: bytes, mime_type: str) -> str:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{b64}"


def _pydantic_to_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return a JSON-Schema dict suitable for response_format.json_schema."""
    schema = model.model_json_schema()
    # Remove Pydantic-specific keys that confuse some proxies.
    schema.pop("title", None)
    return schema


def _build_json_prompt_suffix(model: type[BaseModel]) -> str:
    """Fallback: append explicit JSON instructions to the prompt."""
    schema = model.model_json_schema()
    return (
        "\n\nIMPORTANT: You MUST respond with a single valid JSON object that "
        "strictly conforms to the following JSON Schema. Do NOT include any "
        "markdown fences, commentary, or extra text — only the raw JSON object.\n\n"
        f"Schema:\n{json.dumps(schema, indent=2)}"
    )


# ---------------------------------------------------------------------------
# Main client
# ---------------------------------------------------------------------------

class SutGenAIClient:
    """OpenAI-compatible client pointed at the SUT GenAI gateway."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        api_key = settings.sut_genai_api_key.strip()
        if not api_key:
            raise SutGenAICallError("SUT_GENAI_API_KEY is not set")
        self._client = OpenAI(
            api_key=api_key,
            base_url=_SUT_BASE_URL,
        )

    # ------------------------------------------------------------------
    # generate_structured
    # ------------------------------------------------------------------

    def generate_structured(
        self,
        *,
        model: str,
        prompt: str,
        response_schema: type[T],
        image_bytes: bytes | None = None,
        image_mime_type: str | None = None,
        temperature: float = 0.0,
    ) -> SutGenAICallResult:
        """Call the model and parse the response into *response_schema*.

        Tries ``response_format`` JSON-schema enforcement first; falls back to
        prompt-level JSON instructions if the proxy doesn't honour it.
        """
        request_summary: dict[str, Any] = {
            "model": model,
            "prompt": _truncate(prompt),
            "image": (
                {"mime_type": image_mime_type, "size_bytes": len(image_bytes)}
                if image_bytes
                else None
            ),
            "response_schema": response_schema.__name__,
            "temperature": temperature,
        }

        # Build message content (text + optional image).
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if image_bytes is not None:
            mime = image_mime_type or "image/png"
            data_url = _image_to_data_url(image_bytes, mime)
            content.append({
                "type": "image_url",
                "image_url": {"url": data_url},
            })

        messages = [{"role": "user", "content": content}]

        # --- Attempt 1: response_format with json_schema ---
        raw_text, raw_response = self._call_with_schema(
            model=model,
            messages=messages,
            response_schema=response_schema,
            temperature=temperature,
            request_summary=request_summary,
        )

        parsed = self._try_parse(response_schema, raw_text)

        if parsed is None:
            # --- Attempt 2: prompt-level JSON fallback ---
            logger.warning(
                "SUT GenAI: response_format schema enforcement failed or returned "
                "unparseable JSON for %s — retrying with prompt-level instructions.",
                response_schema.__name__,
            )
            fallback_prompt = prompt + _build_json_prompt_suffix(response_schema)
            fallback_content: list[dict[str, Any]] = [
                {"type": "text", "text": fallback_prompt}
            ]
            if image_bytes is not None:
                fallback_content.append({
                    "type": "image_url",
                    "image_url": {"url": _image_to_data_url(image_bytes, image_mime_type or "image/png")},
                })
            fallback_messages = [{"role": "user", "content": fallback_content}]
            raw_text, raw_response = self._call_plain(
                model=model,
                messages=fallback_messages,
                temperature=temperature,
                request_summary=request_summary,
            )
            parsed = self._try_parse(response_schema, raw_text)

        if parsed is None:
            raise SutGenAICallError(
                f"SUT GenAI returned unparseable JSON for schema {response_schema.__name__}",
                request_summary=request_summary,
                raw_response=raw_text,
            )

        logger.debug(
            "SUT GenAI generate_structured OK schema=%s model=%s",
            response_schema.__name__,
            model,
        )

        return SutGenAICallResult(
            parsed=parsed,
            raw_text=raw_text,
            raw_response=raw_response,
            request_summary=request_summary,
        )

    # ------------------------------------------------------------------
    # generate_text
    # ------------------------------------------------------------------

    def generate_text(
        self,
        *,
        model: str,
        prompt: str,
        image_bytes: bytes | None = None,
        image_mime_type: str | None = None,
        temperature: float = 0.0,
    ) -> SutGenAICallResult:
        """Call the model for a plain-text response (e.g. OCR extraction)."""
        request_summary: dict[str, Any] = {
            "model": model,
            "prompt": _truncate(prompt),
            "image": (
                {"mime_type": image_mime_type, "size_bytes": len(image_bytes)}
                if image_bytes
                else None
            ),
            "temperature": temperature,
        }

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if image_bytes is not None:
            mime = image_mime_type or "image/png"
            data_url = _image_to_data_url(image_bytes, mime)
            content.append({
                "type": "image_url",
                "image_url": {"url": data_url},
            })

        messages = [{"role": "user", "content": content}]
        raw_text, raw_response = self._call_plain(
            model=model,
            messages=messages,
            temperature=temperature,
            request_summary=request_summary,
        )

        if not raw_text or not raw_text.strip():
            raise SutGenAICallError(
                "SUT GenAI returned an empty text response",
                request_summary=request_summary,
                raw_response=raw_response,
            )

        return SutGenAICallResult(
            parsed=None,
            raw_text=raw_text.strip(),
            raw_response=raw_response,
            request_summary=request_summary,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call_with_schema(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        response_schema: type[BaseModel],
        temperature: float,
        request_summary: dict[str, Any],
    ) -> tuple[str | None, Any]:
        """Try calling with response_format json_schema enforcement."""
        json_schema_dict = _pydantic_to_json_schema(response_schema)
        response_format: dict[str, Any] = {
            "type": "json_schema",
            "json_schema": {
                "name": response_schema.__name__,
                "strict": True,
                "schema": json_schema_dict,
            },
        }
        try:
            resp = self._client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature,
                response_format=response_format,  # type: ignore[arg-type]
            )
        except Exception as exc:
            # If the proxy rejects the response_format param entirely, fall
            # through to the plain call path by returning None.
            logger.warning(
                "SUT GenAI: response_format call raised %s: %s — will retry plain.",
                type(exc).__name__,
                exc,
            )
            return None, None

        raw_text = resp.choices[0].message.content if resp.choices else None
        raw_response = resp.model_dump() if hasattr(resp, "model_dump") else str(resp)
        logger.debug(
            "SUT GenAI _call_with_schema raw_text preview: %s",
            (raw_text or "")[:300],
        )
        return raw_text, raw_response

    def _call_plain(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float,
        request_summary: dict[str, Any],
    ) -> tuple[str | None, Any]:
        """Plain chat completion call (no response_format param)."""
        try:
            resp = self._client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature,
            )
        except Exception as exc:
            raise SutGenAICallError(
                f"SUT GenAI API call failed: {exc}",
                request_summary=request_summary,
            ) from exc

        raw_text = resp.choices[0].message.content if resp.choices else None
        raw_response = resp.model_dump() if hasattr(resp, "model_dump") else str(resp)
        logger.debug(
            "SUT GenAI _call_plain raw_text preview: %s",
            (raw_text or "")[:300],
        )
        return raw_text, raw_response

    @staticmethod
    def _try_parse(schema: type[T], raw_text: str | None) -> T | None:
        """Try to parse *raw_text* as JSON into *schema*. Returns None on failure."""
        if not raw_text or not raw_text.strip():
            return None
        text = raw_text.strip()
        # Strip markdown fences if the model wrapped the JSON.
        if text.startswith("```"):
            lines = text.splitlines()
            # Drop first line (```json or ```) and last line (```)
            inner = "\n".join(
                line for line in lines[1:]
                if not line.strip().startswith("```")
            )
            text = inner.strip()
        try:
            return schema.model_validate_json(text)
        except Exception:
            pass
        # Try extracting the first {...} block in case there's surrounding prose.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return schema.model_validate_json(text[start : end + 1])
            except Exception:
                pass
        return None
