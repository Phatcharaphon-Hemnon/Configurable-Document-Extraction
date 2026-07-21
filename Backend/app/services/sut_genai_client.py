"""SUT GenAI gateway client wrapper, now using GitHub Models.

Talks to https://models.github.ai/inference which is an OpenAI-compatible endpoint.

Auth:  Authorization: Bearer <GITHUB_MODELS_TOKEN>
Model: Locked to gpt-4.1 to avoid premium Copilot request quota.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.core.config import Settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_SUT_BASE_URL = "https://models.github.ai/inference"


# ---------------------------------------------------------------------------
# Public exception / result types (mirror the old GeminiCallError interface
# so all existing call sites keep working with a simple import swap).
# ---------------------------------------------------------------------------

class SutGenAICallError(Exception):
    """Raised when an API call fails."""

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

# Uses GitHub Models (OpenAI-compatible endpoint). Locked to gpt-4.1 — this
# model is NOT metered against Copilot Pro's premium request quota (300/month).
# Do not switch to Opus, o3, or GPT-4.5 here without checking premium quota impact
# first, since those carry heavy per-request multipliers.
class SutGenAIClient:
    """OpenAI-compatible client pointed at the GitHub Models endpoint."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        api_key = settings.github_models_token.strip()
        if not api_key:
            raise SutGenAICallError("GITHUB_MODELS_TOKEN is not set")
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=_SUT_BASE_URL,
        )

    # ------------------------------------------------------------------
    # generate_structured
    # ------------------------------------------------------------------

    async def generate_structured(
        self,
        *,
        model: str,
        prompt: str,
        response_schema: type[T],
        temperature: float = 0.0,
    ) -> SutGenAICallResult:
        """Call the model and parse the response into *response_schema*."""
        request_summary: dict[str, Any] = {
            "model": model,
            "prompt": _truncate(prompt),
            "response_schema": response_schema.__name__,
            "temperature": temperature,
        }

        messages = [{"role": "user", "content": prompt}]

        # --- Attempt 1: response_format with json_schema ---
        raw_text, raw_response = await self._call_with_schema(
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
                "GitHub Models: response_format schema enforcement failed or returned "
                "unparseable JSON for %s — retrying with prompt-level instructions.",
                response_schema.__name__,
            )
            fallback_prompt = prompt + _build_json_prompt_suffix(response_schema)
            fallback_messages = [{"role": "user", "content": fallback_prompt}]
            raw_text, raw_response = await self._call_plain(
                model=model,
                messages=fallback_messages,
                temperature=temperature,
                request_summary=request_summary,
            )
            parsed = self._try_parse(response_schema, raw_text)

        if parsed is None:
            raise SutGenAICallError(
                f"GitHub Models returned unparseable JSON for schema {response_schema.__name__}",
                request_summary=request_summary,
                raw_response=raw_text,
            )

        logger.debug(
            "GitHub Models generate_structured OK schema=%s model=%s",
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

    async def generate_text(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float = 0.0,
    ) -> SutGenAICallResult:
        """Call the model for a plain-text response."""
        request_summary: dict[str, Any] = {
            "model": model,
            "prompt": _truncate(prompt),
            "temperature": temperature,
        }

        messages = [{"role": "user", "content": prompt}]
        raw_text, raw_response = await self._call_plain(
            model=model,
            messages=messages,
            temperature=temperature,
            request_summary=request_summary,
        )

        if not raw_text or not raw_text.strip():
            raise SutGenAICallError(
                "GitHub Models returned an empty text response",
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

    async def _call_with_schema(
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
            resp = await self._client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature,
                response_format=response_format,  # type: ignore[arg-type]
            )
        except Exception as exc:
            # If the proxy rejects the response_format param entirely, fall
            # through to the plain call path by returning None.
            logger.warning(
                "GitHub Models: response_format call raised %s: %s — will retry plain.",
                type(exc).__name__,
                exc,
            )
            return None, None

        raw_text = resp.choices[0].message.content if resp.choices else None
        raw_response = resp.model_dump() if hasattr(resp, "model_dump") else str(resp)
        logger.debug(
            "GitHub Models _call_with_schema raw_text preview: %s",
            (raw_text or "")[:300],
        )
        return raw_text, raw_response

    async def _call_plain(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float,
        request_summary: dict[str, Any],
    ) -> tuple[str | None, Any]:
        """Plain chat completion call (no response_format param)."""
        try:
            resp = await self._client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature,
            )
        except Exception as exc:
            raise SutGenAICallError(
                f"GitHub Models API call failed: {exc}",
                request_summary=request_summary,
            ) from exc

        raw_text = resp.choices[0].message.content if resp.choices else None
        raw_response = resp.model_dump() if hasattr(resp, "model_dump") else str(resp)
        logger.debug(
            "GitHub Models _call_plain raw_text preview: %s",
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
