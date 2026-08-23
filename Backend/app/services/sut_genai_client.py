"""SUT GenAI gateway client wrapper, now using OpenRouter.

Talks to https://openrouter.ai/api/v1 which is an OpenAI-compatible endpoint.

Auth:  Authorization: Bearer <OPENROUTER_API_KEY>
Model: Locked to nvidia/nemotron-nano-12b-v2-vl:free to avoid premium Copilot request quota.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from typing import Any, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.core.config import Settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_SUT_BASE_URL = "https://openrouter.ai/api/v1"


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
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


# Keep old name as alias.
GeminiCallResult = SutGenAICallResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _truncate(value: str, limit: int = 500) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}… [truncated, {len(value)} chars total]"


def _patch_schema_for_strict_mode(schema: dict) -> None:
    """Recursively set strict mode constraints on all object schemas.
    
    1. Sets additionalProperties = False
    2. Forces all properties into the required array
    """
    if not isinstance(schema, dict):
        return

    if schema.get("type") == "object":
        schema["additionalProperties"] = False
        if "properties" in schema and isinstance(schema["properties"], dict):
            schema["required"] = list(schema["properties"].keys())

    if "properties" in schema and isinstance(schema["properties"], dict):
        for prop_schema in schema["properties"].values():
            _patch_schema_for_strict_mode(prop_schema)

    if "$defs" in schema and isinstance(schema["$defs"], dict):
        for def_schema in schema["$defs"].values():
            _patch_schema_for_strict_mode(def_schema)

    if "items" in schema and isinstance(schema["items"], dict):
        _patch_schema_for_strict_mode(schema["items"])

    for key in ["anyOf", "oneOf", "allOf"]:
        if key in schema and isinstance(schema[key], list):
            for branch in schema[key]:
                _patch_schema_for_strict_mode(branch)


def _pydantic_to_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return a JSON-Schema dict suitable for response_format.json_schema."""
    schema = model.model_json_schema()
    # Remove Pydantic-specific keys that confuse some proxies.
    schema.pop("title", None)
    
    _patch_schema_for_strict_mode(schema)
    
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

# Uses OpenRouter (OpenAI-compatible endpoint). Locked to nvidia/nemotron-nano-12b-v2-vl:free — this
# model is NOT metered against Copilot Pro's premium request quota (300/month).
# Do not switch to Opus, o3, or GPT-4.5 here without checking premium quota impact
# first, since those carry heavy per-request multipliers.
class SutGenAIClient:
    """OpenAI-compatible client pointed at the OpenRouter endpoint."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        api_key = settings.openrouter_api_key.strip()
        if not api_key:
            raise SutGenAICallError("OPENROUTER_API_KEY is not set")
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
        raw_text, raw_response, attempt1_prompt_tokens, attempt1_comp_tokens, attempt1_tot_tokens = await self._call_with_schema(
            model=model,
            messages=messages,
            response_schema=response_schema,
            temperature=temperature,
            request_summary=request_summary,
        )

        prompt_tokens = attempt1_prompt_tokens
        completion_tokens = attempt1_comp_tokens
        total_tokens = attempt1_tot_tokens

        parsed = self._try_parse(response_schema, raw_text)

        if parsed is None:
            # --- Attempt 2: prompt-level JSON fallback ---
            if raw_text is None:
                fallback_prompt = prompt + _build_json_prompt_suffix(response_schema)
            else:
                fallback_prompt = (
                    "The following response was supposed to be valid JSON matching a "
                    "specific schema, but failed to parse. Return ONLY the corrected, "
                    "valid JSON object — no markdown fences, no commentary.\n\n"
                    f"Schema:\n{json.dumps(response_schema.model_json_schema(), indent=2)}\n\n"
                    f"Response to fix:\n{raw_text}"
                )
            fallback_messages = [{"role": "user", "content": fallback_prompt}]
            raw_text, raw_response, attempt2_prompt_tokens, attempt2_comp_tokens, attempt2_tot_tokens = await self._call_plain(
                model=model,
                messages=fallback_messages,
                temperature=temperature,
                request_summary=request_summary,
            )
            logger.warning(
                "OpenRouter retry triggered for schema=%s — extra tokens spent: "
                "attempt1=%s prompt tokens, attempt2=%s prompt tokens",
                response_schema.__name__,
                attempt1_prompt_tokens,
                attempt2_prompt_tokens,
            )
            parsed = self._try_parse(response_schema, raw_text)

            prompt_tokens = attempt2_prompt_tokens
            completion_tokens = attempt2_comp_tokens
            total_tokens = attempt2_tot_tokens

        if parsed is None:
            raise SutGenAICallError(
                f"OpenRouter returned unparseable JSON for schema {response_schema.__name__}",
                request_summary=request_summary,
                raw_response=raw_text,
            )

        logger.debug(
            "OpenRouter generate_structured OK schema=%s model=%s",
            response_schema.__name__,
            model,
        )

        return SutGenAICallResult(
            parsed=parsed,
            raw_text=raw_text,
            raw_response=raw_response,
            request_summary=request_summary,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )

    # ------------------------------------------------------------------
    # generate_structured_with_image (vision / multimodal)
    # ------------------------------------------------------------------

    async def generate_structured_with_image(
        self,
        *,
        model: str,
        prompt: str,
        image_bytes: bytes,
        image_media_type: str,
        response_schema: type[T],
        temperature: float = 0.0,
    ) -> SutGenAICallResult:
        """Call the model with an image + text prompt and parse the response.

        Uses the OpenAI vision API format: the user message contains a list
        of content parts — one ``image_url`` (base64 data-URI) and one
        ``text`` part.
        """
        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_uri = f"data:{image_media_type};base64,{b64}"

        request_summary: dict[str, Any] = {
            "model": model,
            "prompt": _truncate(prompt),
            "response_schema": response_schema.__name__,
            "temperature": temperature,
            "has_image": True,
            "image_size_bytes": len(image_bytes),
        }

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_uri, "detail": "high"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ]

        # --- Attempt 1: response_format with json_schema ---
        raw_text, raw_response, a1_pt, a1_ct, a1_tt = await self._call_with_schema(
            model=model,
            messages=messages,
            response_schema=response_schema,
            temperature=temperature,
            request_summary=request_summary,
        )

        prompt_tokens = a1_pt
        completion_tokens = a1_ct
        total_tokens = a1_tt

        parsed = self._try_parse(response_schema, raw_text)

        if parsed is None:
            # --- Attempt 2: prompt-level JSON fallback ---
            if raw_text is None:
                fallback_prompt = prompt + _build_json_prompt_suffix(response_schema)
                fallback_messages = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_uri, "detail": "high"}},
                            {"type": "text", "text": fallback_prompt},
                        ],
                    }
                ]
            else:
                fallback_prompt = (
                    "The following response was supposed to be valid JSON matching a "
                    "specific schema, but failed to parse. Return ONLY the corrected, "
                    "valid JSON object — no markdown fences, no commentary.\n\n"
                    f"Schema:\n{json.dumps(response_schema.model_json_schema(), indent=2)}\n\n"
                    f"Response to fix:\n{raw_text}"
                )
                fallback_messages = [{"role": "user", "content": fallback_prompt}]
            raw_text, raw_response, a2_pt, a2_ct, a2_tt = await self._call_plain(
                model=model,
                messages=fallback_messages,
                temperature=temperature,
                request_summary=request_summary,
            )
            logger.warning(
                "OpenRouter vision retry triggered for schema=%s — extra tokens spent",
                response_schema.__name__,
            )
            parsed = self._try_parse(response_schema, raw_text)
            prompt_tokens = a2_pt
            completion_tokens = a2_ct
            total_tokens = a2_tt

        if parsed is None:
            raise SutGenAICallError(
                f"OpenRouter returned unparseable JSON for schema {response_schema.__name__} (vision) [image_size_bytes={len(image_bytes)}, image_media_type={image_media_type}]",
                request_summary=request_summary,
                raw_response=raw_text,
            )

        logger.debug(
            "OpenRouter generate_structured_with_image OK schema=%s model=%s",
            response_schema.__name__,
            model,
        )

        return SutGenAICallResult(
            parsed=parsed,
            raw_text=raw_text,
            raw_response=raw_response,
            request_summary=request_summary,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
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
        raw_text, raw_response, prompt_tokens, completion_tokens, total_tokens = await self._call_plain(
            model=model,
            messages=messages,
            temperature=temperature,
            request_summary=request_summary,
        )

        if not raw_text or not raw_text.strip():
            raise SutGenAICallError(
                "OpenRouter returned an empty text response",
                request_summary=request_summary,
                raw_response=raw_response,
            )

        return SutGenAICallResult(
            parsed=None,
            raw_text=raw_text.strip(),
            raw_response=raw_response,
            request_summary=request_summary,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
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
    ) -> tuple[str | None, Any, int | None, int | None, int | None]:
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
                "OpenRouter: response_format call raised %s: %s — will retry plain.",
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            return None, None, None, None, None

        raw_text = resp.choices[0].message.content if resp.choices else None
        raw_response = resp.model_dump() if hasattr(resp, "model_dump") else str(resp)
        usage = getattr(resp, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
        total_tokens = getattr(usage, "total_tokens", None) if usage else None
        logger.debug(
            "OpenRouter _call_with_schema raw_text preview: %s",
            (raw_text or "")[:300],
        )
        return raw_text, raw_response, prompt_tokens, completion_tokens, total_tokens

    async def _call_plain(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float,
        request_summary: dict[str, Any],
    ) -> tuple[str | None, Any, int | None, int | None, int | None]:
        """Plain chat completion call (no response_format param)."""
        try:
            resp = await self._client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature,
            )
        except Exception as exc:
            raise SutGenAICallError(
                f"OpenRouter API call failed: {exc}",
                request_summary=request_summary,
            ) from exc

        raw_text = resp.choices[0].message.content if resp.choices else None
        raw_response = resp.model_dump() if hasattr(resp, "model_dump") else str(resp)
        usage = getattr(resp, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
        total_tokens = getattr(usage, "total_tokens", None) if usage else None
        logger.debug(
            "OpenRouter _call_plain raw_text preview: %s",
            (raw_text or "")[:300],
        )
        return raw_text, raw_response, prompt_tokens, completion_tokens, total_tokens

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
