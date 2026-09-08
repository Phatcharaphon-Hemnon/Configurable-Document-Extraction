"""Generic LLM client wrapper (OpenAI-compatible chat.completions).

Target is chosen by Settings: LLM_PROVIDER selects the base URL
(openai / ollama-cloud / ollama-local) and LLM_MODEL the model.

Auth:  Authorization: Bearer <LLM_API_KEY>
Model: configurable via LLM_MODEL (per-stage ROUTER/EXTRACTION/JUDGE_MODEL_NAME
       overrides supported).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, TypeVar

from openai import APITimeoutError, AsyncOpenAI
from pydantic import BaseModel

from app.core.config import Settings
from app.services.request_control import (
    job_context,
    limited_generation,
    request_budget,
    stage_context,
    stage_deadline,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_DEFAULT_BASE_URL = "https://api.openai.com/v1"


# ---------------------------------------------------------------------------
# Public exception / result types.
# ---------------------------------------------------------------------------

class ClientError(Exception):
    """Raised when an API call fails.

    Carries redacted provider details so callers can surface the real
    gateway message (status/code/request_id) instead of only the exception
    type name. See extract_provider_error().
    """

    def __init__(
        self,
        message: str,
        *,
        request_summary: dict[str, Any] | None = None,
        raw_response: Any = None,
        provider_details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.request_summary = request_summary or {}
        self.raw_response = raw_response
        self.provider_details = provider_details or {}


@dataclass(slots=True)
class ClientResult:
    parsed: BaseModel | None
    raw_text: str | None
    raw_response: Any
    request_summary: dict[str, Any]
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _truncate(value: str, limit: int = 500) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}… [truncated, {len(value)} chars total]"


# Full provider errors live in the OpenAI SDK exception body
# (body.error.message/code), NOT in str(exc) — str(exc) is only the short
# message. This helper extracts a redacted, JSON-safe dict so the pipeline
# can surface status/code/request_id end to end. See docs/provider_errors.md.
PROVIDER_MESSAGE_LIMIT = 2000

_SECRET_PATTERNS = (
    re.compile(r"(api[_-]?key|secret|token|password)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/=]+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9\-_]{8,}\b"),
    re.compile(r"\bllx-[A-Za-z0-9\-_]{8,}\b"),
)


def _redact_provider_text(value: str) -> str:
    """Strip secrets from provider messages (keys/tokens stay server-side)."""
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def extract_provider_error(exc: BaseException) -> dict[str, Any]:
    """Extract redacted provider details from an OpenAI-compatible SDK error.

    Handles both body shapes: flat {"message": ...} and nested
    {"error": {"message/code/param/type": ...}}. Never includes raw bodies,
    headers, or keys — only status/code/message/request_id for display.
    """
    error_type = type(exc).__name__
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = None
    request_id = getattr(exc, "request_id", None)
    if not isinstance(request_id, str) or not request_id:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        try:
            request_id = headers.get("x-request-id") if headers else None
        except Exception:
            request_id = None
    if not isinstance(request_id, str) or not request_id:
        request_id = None

    code: Any = getattr(exc, "code", None)
    param: Any = getattr(exc, "param", None)
    err_type: Any = getattr(exc, "type", None)
    message: Any = getattr(exc, "message", None)
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        nested = body.get("error")
        err_obj = nested if isinstance(nested, dict) else body
        if isinstance(err_obj.get("message"), str) and err_obj["message"]:
            message = err_obj["message"]
        if err_obj.get("code") is not None:
            code = err_obj.get("code")
        if err_obj.get("param") is not None:
            param = err_obj.get("param")
        if err_obj.get("type") is not None:
            err_type = err_obj.get("type")
    if not isinstance(message, str) or not message:
        message = str(exc) or error_type
    message = _redact_provider_text(message)
    if len(message) > PROVIDER_MESSAGE_LIMIT:
        message = f"{message[:PROVIDER_MESSAGE_LIMIT]}… [truncated, {len(message)} chars total]"

    details: dict[str, Any] = {"error_type": error_type, "message": message}
    if status is not None:
        details["status"] = status
    if isinstance(code, str) and code:
        details["code"] = code
    if isinstance(param, str) and param:
        details["param"] = param
    if isinstance(err_type, str) and err_type:
        details["type"] = err_type
    if request_id:
        details["request_id"] = request_id
    return details


_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think>", re.IGNORECASE)


def _strip_think_blocks(text: str) -> str:
    """Remove reasoning-model <think>...</think> blocks from raw output.

    Handles multiple blocks and a truncated (unclosed) trailing block:
    an unclosed "<think>" marker and everything after it up to the last
    JSON-looking brace is dropped conservatively — actually we keep the
    text after the marker (reasoning text is removed, JSON kept) by
    stripping only the marker itself when no closing tag exists.
    """
    if not text or "<think" not in text.lower():
        return text
    # Remove all closed blocks first.
    stripped = _THINK_BLOCK_RE.sub("", text)
    # If an unclosed <think> remains, drop the marker but keep the rest
    # (the JSON payload usually follows the truncated reasoning).
    if _THINK_OPEN_RE.search(stripped):
        stripped = _THINK_OPEN_RE.sub("", stripped)
    return stripped


# Provider billing / quota errors fail fast: retrying or degrading tiers
# only burns calls — the account needs billing action. Per-minute
# throttling (rate_limit_exceeded, 429 rate limit) must keep backing off.
# Keys mirror app.core.config.LLM_PROVIDERS ("": no billing page, e.g. local).
_BILLING_URLS = {
    "openai": "https://platform.openai.com/account/billing",
    "xai": "https://console.x.ai",
    "gemini": "https://aistudio.google.com",
    "openrouter": "https://openrouter.ai/settings/credits",
    "deepseek": "https://platform.deepseek.com/usage",
    "kimi": "https://platform.moonshot.ai/console",
    "ollama-cloud": "https://ollama.com/settings",
    "ollama-local": "",
    "mistral": "https://console.mistral.ai",
    "openclaw": "",
    "opencode": "https://opencode.ai/zen",
}
_PAYMENT_MARKERS = (
    "insufficient_quota",
    "billing_hard_limit",
    "billing account",
    "you exceeded your current quota",
    "no active payment",
    "billing_hard_limit_exceeded",
)
_PER_DAY_QUOTA_MARKERS = (
    "per day",
    "perday",
    "daily",
    "limit 250 per day",
)


def _is_payment_error(exc: BaseException) -> bool:
    """True for provider billing/quota errors that will not recover on retry."""
    text = f"{type(exc).__name__} {exc}".lower()
    if any(marker in text for marker in _PAYMENT_MARKERS):
        # Per-minute throttling mentions rate limits without billing —
        # never treat those as payment errors.
        if "perminute" in text.replace("-", "").replace("_", "").replace(" ", "") and "freelimit" in text.replace("-", "").replace(" ", ""):
            return False
        return True
    if any(marker in text for marker in ("weekly usage limit", "daily usage limit", "credits exhausted")):
        return True
    # Legacy daily-quota shape ("Quota exceeded ... per day") fails fast.
    if "quota exceeded" in text and any(m in text for m in _PER_DAY_QUOTA_MARKERS):
        return True
    # Bare 403 (no rate-limit marker) is an auth/billing stop, not throttling.
    if "403" in text and "rate limit" not in text and "429" not in text:
        return True
    return False


def _billing_url(settings: Settings) -> str:
    """Billing dashboard for the active provider ("" when N/A, e.g. local)."""
    provider = _get_setting_str(settings, "llm_provider", "openai").lower() or "openai"
    return _BILLING_URLS.get(provider, _BILLING_URLS["openai"])


def _payment_error_message(exc: BaseException, billing_url: str) -> str:
    text = str(exc)
    hint = f" See {billing_url}" if billing_url else ""
    if "per day" in text.lower() or "perday" in text.lower().replace(" ", ""):
        return (
            f"LLM billing/quota exhausted (daily limit): {text[:200]}."
            f" Resets at midnight Pacific.{hint}"
        )
    return (
        f"LLM billing/quota error: {text[:200]}. "
        f"Enable billing or check usage.{hint}"
    )


# Provider rate-limit handling: transient 429/503/overloaded errors back
# off and retry the SAME call instead of degrading tiers. "quota exceeded"
# alone is throttling (retry); billing-specific quota shapes are detected
# by _is_payment_error first and never reach here as rate limits.
_RATE_LIMIT_MARKERS = ("429", "503", "resourceexhausted", "rate limit", "request limit", "overloaded", "quota exceeded")
RATE_LIMIT_MAX_RETRIES = 4
RATE_LIMIT_BACKOFF_SECONDS = 3.0

# Transient gateway stalls (no response within the request timeout) are
# retried on the SAME tier before falling through to the next output mode:
# a stall says nothing about the request shape, while the next tier returns
# differently-shaped output. Probe: api/scripts/time_gateway_modes.py.
TIMEOUT_MAX_RETRIES = 1
TIMEOUT_RETRY_BACKOFF_SECONDS = 2.0


def _is_rate_limit_error(exc: BaseException) -> bool:
    if _is_payment_error(exc):
        return False
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in (429, 503)
    text = f"{type(exc).__name__} {exc}".lower()
    return any(marker in text for marker in _RATE_LIMIT_MARKERS)


def _unsupported_parameter(exc: Exception, names: tuple[str, ...]) -> bool:
    status = getattr(exc, "status_code", None)
    if status not in (400, 422):
        return False
    message = str(exc).lower()
    return any(name in message for name in names) and any(
        marker in message for marker in ("unsupported", "not supported", "unrecognized", "unknown", "not allowed")
    )


def _retry_after_seconds(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {})
    value = headers.get("retry-after")
    if not isinstance(value, str):
        return None
    try:
        seconds = float(value)
        return seconds if 0 <= seconds < float("inf") else None
    except ValueError:
        try:
            return max(0.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return None


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
    """Compact output contract for every mode, including schema-optional providers."""
    schema = model.model_json_schema()
    return (
        "\n\nIMPORTANT: You MUST respond with a single valid JSON object that "
        "strictly conforms to the following JSON Schema. Do NOT include any "
        "markdown fences, commentary, or extra text — only the raw JSON object.\n\n"
        f"Schema:\n{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}"
    )


def _get_setting_str(settings: Settings, name: str, default: str = "") -> str:
    value = getattr(settings, name, default)
    if not isinstance(value, str):
        return default
    value = value.strip()
    return value or default


def _resolve_temperature(settings: Settings, temperature: float | None) -> float:
    """Method-level temperature wins; otherwise the provider default."""
    if isinstance(temperature, (int, float)):
        return float(temperature)
    fallback = getattr(settings, "llm_temperature", 0.0)
    return float(fallback) if isinstance(fallback, (int, float)) else 0.0


# ---------------------------------------------------------------------------
# Main client
# ---------------------------------------------------------------------------

class Client:
    """Provider-agnostic LLM client (OpenAI-compatible chat.completions).

    Target is chosen by Settings: LLM_PROVIDER selects the base URL
    (openai / ollama-cloud / ollama-local) and LLM_MODEL the model.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._timeout = settings.llm_request_timeout_seconds
        # Token counts of the most recent successful call, for observability.
        # Read synchronously right after awaiting a call (same task) — the
        # service attaches these to Langfuse generations for cost tracking.
        self.last_usage: dict[str, int | None] | None = None
        raw_key = _get_setting_str(settings, "llm_api_key")
        api_key = raw_key or "dummy-key"
        base_url = _get_setting_str(settings, "llm_base_url", _DEFAULT_BASE_URL)
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=self._timeout,
            max_retries=0,  # one shared generation budget owns retries
        )

    @limited_generation
    async def _chat_with_retry(self, **kwargs: Any):
        """Retry transient failures within the generation's shared HTTP budget."""
        budget = request_budget.get()
        assert budget is not None
        started = asyncio.get_running_loop().time()
        while budget.attempts < RATE_LIMIT_MAX_RETRIES:
            budget.attempts += 1
            logger.info(
                "LLM request job=%s stage=%s attempt=%d/%d elapsed=%.1fs",
                job_context.get(), stage_context.get(), budget.attempts,
                RATE_LIMIT_MAX_RETRIES, asyncio.get_running_loop().time() - started,
            )
            try:
                return await asyncio.wait_for(
                    self._client.chat.completions.create(**kwargs), timeout=self._timeout,
                )
            except Exception as exc:
                if _is_payment_error(exc):
                    raise
                if isinstance(exc, (asyncio.TimeoutError, APITimeoutError)):
                    if budget.timeout_retries >= TIMEOUT_MAX_RETRIES:
                        raise asyncio.TimeoutError() from exc
                    budget.timeout_retries += 1
                    delay = TIMEOUT_RETRY_BACKOFF_SECONDS
                elif _is_rate_limit_error(exc):
                    delay = RATE_LIMIT_BACKOFF_SECONDS * 2 ** (budget.attempts - 1)
                    delay += random.uniform(0, 0.5)
                    retry_after = _retry_after_seconds(exc)
                    if retry_after is not None:
                        delay = max(delay, retry_after)
                else:
                    raise
                if budget.attempts >= RATE_LIMIT_MAX_RETRIES:
                    raise
                deadline = stage_deadline.get()
                if deadline is not None and asyncio.get_running_loop().time() + delay >= deadline:
                    raise ClientError("Provider retry delay exceeds remaining stage deadline") from exc
                logger.warning(
                    "LLM retry job=%s stage=%s attempt=%d/%d reason=%s delay=%.1fs details=%s",
                    job_context.get(), stage_context.get(), budget.attempts,
                    RATE_LIMIT_MAX_RETRIES, type(exc).__name__, delay,
                    extract_provider_error(exc),
                )
                await asyncio.sleep(delay)
        raise ClientError("LLM request budget exhausted (4 HTTP attempts)")

    async def _request_mode(self, kwargs: dict[str, Any], request_summary: dict[str, Any]):
        """Only explicit parameter incompatibility permits changing a request."""
        try:
            return await self._chat_with_retry(**kwargs)
        except (asyncio.TimeoutError, APITimeoutError) as exc:
            raise ClientError(
                "LLM request timed out",
                request_summary=request_summary,
                provider_details={**extract_provider_error(exc), "timeout": True},
            ) from exc
        except Exception as exc:
            self._fail_fast_if_payment_error(exc, request_summary)
            if "extra_body" in kwargs and _unsupported_parameter(exc, ("reasoning", "extra_body")):
                # Retry only an explicitly rejected optional parameter.
                kwargs = {key: value for key, value in kwargs.items() if key != "extra_body"}
                return await self._request_mode(kwargs, request_summary)
            if "response_format" in kwargs and _unsupported_parameter(
                exc, ("response_format", "json_schema", "json_object")
            ):
                return None
            if isinstance(exc, ClientError):
                raise
            details = extract_provider_error(exc)
            # Log the full redacted provider body server-side; the raised
            # message stays short for UI display, details travel structured.
            logger.error(
                "LLM provider error job=%s stage=%s model=%s status=%s code=%s request_id=%s message=%.2000s",
                job_context.get(), stage_context.get(),
                kwargs.get("model"), details.get("status"),
                details.get("code"), details.get("request_id"),
                details.get("message", ""),
            )
            reason = "Provider rate limit exhausted" if _is_rate_limit_error(exc) else "LLM API call failed"
            raise ClientError(
                f"{reason} ({details.get('error_type', type(exc).__name__)})",
                request_summary=request_summary,
                provider_details=details,
            ) from exc

    def _record_usage(
        self,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        total_tokens: int | None,
    ) -> None:
        """Stash token counts of the latest successful call for observability."""
        self.last_usage = {
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    def _fail_fast_if_payment_error(self, exc: Exception, request_summary: dict[str, Any]) -> None:
        """Raise ClientError immediately for billing errors (no tier fallback)."""
        if _is_payment_error(exc):
            raise ClientError(
                _payment_error_message(exc, _billing_url(self.settings)),
                request_summary=request_summary,
                provider_details=extract_provider_error(exc),
            ) from exc

    # ------------------------------------------------------------------
    # generate_structured
    # ------------------------------------------------------------------

    @limited_generation
    async def generate_structured(
        self,
        *,
        model: str,
        prompt: str,
        response_schema: type[T],
        temperature: float | None = None,
        max_tokens: int | None = None,
        disable_reasoning: bool = False,
    ) -> ClientResult:
        """Call the model and parse the response into *response_schema*."""
        temperature = _resolve_temperature(self.settings, temperature)
        request_summary: dict[str, Any] = {
            "model": model,
            "prompt": _truncate(prompt),
            "response_schema": response_schema.__name__,
            "temperature": temperature,
            "disable_reasoning": disable_reasoning,
        }

        messages = [{"role": "user", "content": prompt + _build_json_prompt_suffix(response_schema)}]

        # --- Attempt 1: response_format with json_schema ---
        if not getattr(self.settings, "disable_strict_json_schema", False):
            logger.info(
                "Attempt 1 (schema): model=%s schema=%s disable_reasoning=%s",
                model, response_schema.__name__, disable_reasoning,
            )
            raw_text, raw_response, prompt_tokens, completion_tokens, total_tokens = await self._call_with_schema(
                model=model,
                messages=messages,
                response_schema=response_schema,
                temperature=temperature,
                request_summary=request_summary,
                max_tokens=max_tokens,
                disable_reasoning=disable_reasoning,
            )
            parsed = self._try_parse(response_schema, raw_text)
        else:
            logger.info(
                "Skipping Attempt 1 (schema) due to DISABLE_STRICT_JSON_SCHEMA=true for model=%s schema=%s",
                model, response_schema.__name__,
            )
            raw_text, raw_response, prompt_tokens, completion_tokens, total_tokens = None, None, None, None, None
            parsed = None

        # --- Attempt 2: response_format with json_object ---
        if parsed is None:
            logger.info(
                "Attempt 2 (json_object): model=%s schema=%s",
                model, response_schema.__name__,
            )
            logger.warning(
                "LLM retry (json_object) triggered for schema=%s — attempt 1 returned invalid JSON",
                response_schema.__name__,
            )
            raw_text, raw_response, prompt_tokens, completion_tokens, total_tokens = await self._call_with_json_object_mode(
                model=model,
                messages=messages,
                temperature=temperature,
                request_summary=request_summary,
                max_tokens=max_tokens,
                disable_reasoning=disable_reasoning,
            )
            parsed = self._try_parse(response_schema, raw_text)

        # --- Attempt 3: prompt-level JSON fallback ---
        if parsed is None:
            logger.info(
                "Attempt 3 (plain prompt fallback): model=%s schema=%s",
                model, response_schema.__name__,
            )
            logger.warning(
                "LLM retry (plain) triggered for schema=%s — attempt 2 returned invalid JSON",
                response_schema.__name__,
            )
            fallback_messages = [*messages, {
                "role": "user",
                "content": "The previous response failed schema validation. Repeat the original task "
                           "using only the original source data. Return only JSON matching the supplied Schema.",
            }]
            raw_text, raw_response, prompt_tokens, completion_tokens, total_tokens = await self._call_plain(
                model=model,
                messages=fallback_messages,
                temperature=temperature,
                request_summary=request_summary,
                max_tokens=max_tokens,
                disable_reasoning=disable_reasoning,
            )
            parsed = self._try_parse(response_schema, raw_text)

        if parsed is None:
            raise ClientError(
                f"LLM returned unparseable JSON for schema {response_schema.__name__}. "
                f"Raw response preview: {_truncate(raw_text or '(empty response)', 300)}",
                request_summary=request_summary,
                raw_response=raw_text,
            )

        logger.debug(
            "LLM generate_structured OK schema=%s model=%s",
            response_schema.__name__,
            model,
        )

        self._record_usage(prompt_tokens, completion_tokens, total_tokens)
        return ClientResult(
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

    @limited_generation
    async def generate_structured_with_image(
        self,
        *,
        model: str,
        prompt: str,
        image_bytes: bytes,
        image_media_type: str,
        response_schema: type[T],
        temperature: float | None = None,
        max_tokens: int | None = None,
        disable_reasoning: bool = False,
    ) -> ClientResult:
        """Call the model with an image + text prompt and parse the response.

        Uses the OpenAI vision API format: the user message contains a list
        of content parts — one ``image_url`` (base64 data-URI) and one
        ``text`` part.
        """
        temperature = _resolve_temperature(self.settings, temperature)
        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_uri = f"data:{image_media_type};base64,{b64}"

        request_summary: dict[str, Any] = {
            "model": model,
            "prompt": _truncate(prompt),
            "response_schema": response_schema.__name__,
            "temperature": temperature,
            "has_image": True,
            "image_size_bytes": len(image_bytes),
            "disable_reasoning": disable_reasoning,
        }

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_uri, "detail": "high"}},
                    {"type": "text", "text": prompt + _build_json_prompt_suffix(response_schema)},
                ],
            }
        ]

        # --- Attempt 1: response_format with json_schema ---
        if not getattr(self.settings, "disable_strict_json_schema", False):
            logger.info(
                "Attempt 1 (schema+vision): model=%s schema=%s image_size=%d disable_reasoning=%s",
                model, response_schema.__name__, len(image_bytes), disable_reasoning,
            )
            raw_text, raw_response, prompt_tokens, completion_tokens, total_tokens = await self._call_with_schema(
                model=model,
                messages=messages,
                response_schema=response_schema,
                temperature=temperature,
                request_summary=request_summary,
                max_tokens=max_tokens,
                disable_reasoning=disable_reasoning,
            )
            parsed = self._try_parse(response_schema, raw_text)
        else:
            logger.info(
                "Skipping Attempt 1 (schema+vision) due to DISABLE_STRICT_JSON_SCHEMA=true for model=%s schema=%s",
                model, response_schema.__name__,
            )
            raw_text, raw_response, prompt_tokens, completion_tokens, total_tokens = None, None, None, None, None
            parsed = None

        # --- Attempt 2: response_format with json_object ---
        if parsed is None:
            logger.info(
                "Attempt 2 (json_object+vision): model=%s schema=%s image_size=%d",
                model, response_schema.__name__, len(image_bytes),
            )
            logger.warning(
                "OpenAI vision retry (json_object) triggered for schema=%s — attempt 1 returned invalid JSON",
                response_schema.__name__,
            )
            raw_text, raw_response, prompt_tokens, completion_tokens, total_tokens = await self._call_with_json_object_mode(
                model=model,
                messages=messages,
                temperature=temperature,
                request_summary=request_summary,
                max_tokens=max_tokens,
                disable_reasoning=disable_reasoning,
            )
            parsed = self._try_parse(response_schema, raw_text)

        # --- Attempt 3: prompt-level JSON fallback ---
        if parsed is None:
            logger.info(
                "Attempt 3 (plain prompt fallback+vision): model=%s schema=%s",
                model, response_schema.__name__,
            )
            logger.warning(
                "OpenAI vision retry (plain) triggered for schema=%s — attempt 2 returned invalid JSON",
                response_schema.__name__,
            )
            fallback_messages = [*messages, {
                "role": "user",
                "content": "The previous response failed schema validation. Repeat the original task "
                           "using only the original source data. Return only JSON matching the supplied Schema.",
            }]
            raw_text, raw_response, prompt_tokens, completion_tokens, total_tokens = await self._call_plain(
                model=model,
                messages=fallback_messages,
                temperature=temperature,
                request_summary=request_summary,
                max_tokens=max_tokens,
                disable_reasoning=disable_reasoning,
            )
            parsed = self._try_parse(response_schema, raw_text)

        if parsed is None:
            raise ClientError(
                f"LLM returned unparseable JSON for schema {response_schema.__name__} "
                f"(vision) [image_size_bytes={len(image_bytes)}, image_media_type={image_media_type}]. "
                f"Raw response preview: {_truncate(raw_text or '(empty response)', 300)}",
                request_summary=request_summary,
                raw_response=raw_text,
            )

        logger.debug(
            "LLM generate_structured_with_image OK schema=%s model=%s",
            response_schema.__name__,
            model,
        )

        self._record_usage(prompt_tokens, completion_tokens, total_tokens)
        return ClientResult(
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

    @limited_generation
    async def generate_text(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float | None = None,
    ) -> ClientResult:
        """Call the model for a plain-text response."""
        temperature = _resolve_temperature(self.settings, temperature)
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
            raise ClientError(
                "LLM returned an empty text response",
                request_summary=request_summary,
                raw_response=raw_response,
            )

        self._record_usage(prompt_tokens, completion_tokens, total_tokens)
        return ClientResult(
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
        max_tokens: int | None = None,
        disable_reasoning: bool = False,
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
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "response_format": response_format,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if disable_reasoning:
            kwargs["extra_body"] = {"reasoning": {"enabled": False}}
        resp = await self._request_mode(kwargs, request_summary)
        if resp is None:
            return None, None, None, None, None

        raw_text = resp.choices[0].message.content if resp.choices else None
        raw_response = resp.model_dump() if hasattr(resp, "model_dump") else str(resp)
        usage = getattr(resp, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
        total_tokens = getattr(usage, "total_tokens", None) if usage else None
        return raw_text, raw_response, prompt_tokens, completion_tokens, total_tokens

    async def _call_with_json_object_mode(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float,
        request_summary: dict[str, Any],
        max_tokens: int | None = None,
        disable_reasoning: bool = False,
    ) -> tuple[str | None, Any, int | None, int | None, int | None]:
        """Try calling with response_format={"type": "json_object"} enforcement."""
        response_format: dict[str, Any] = {"type": "json_object"}
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "response_format": response_format,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if disable_reasoning:
            kwargs["extra_body"] = {"reasoning": {"enabled": False}}
        resp = await self._request_mode(kwargs, request_summary)
        if resp is None:
            return None, None, None, None, None

        raw_text = resp.choices[0].message.content if resp.choices else None
        raw_response = resp.model_dump() if hasattr(resp, "model_dump") else str(resp)
        usage = getattr(resp, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
        total_tokens = getattr(usage, "total_tokens", None) if usage else None
        return raw_text, raw_response, prompt_tokens, completion_tokens, total_tokens

    async def _call_plain(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float,
        request_summary: dict[str, Any],
        max_tokens: int | None = None,
        disable_reasoning: bool = False,
    ) -> tuple[str | None, Any, int | None, int | None, int | None]:
        """Plain chat completion call (no response_format param)."""
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if disable_reasoning:
            kwargs["extra_body"] = {"reasoning": {"enabled": False}}
        resp = await self._request_mode(kwargs, request_summary)
        if resp is None:
            return None, None, None, None, None

        raw_text = resp.choices[0].message.content if resp.choices else None
        raw_response = resp.model_dump() if hasattr(resp, "model_dump") else str(resp)
        usage = getattr(resp, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
        total_tokens = getattr(usage, "total_tokens", None) if usage else None
        return raw_text, raw_response, prompt_tokens, completion_tokens, total_tokens

    @staticmethod
    def _try_parse(schema: type[T], raw_text: str | None) -> T | None:
        """Try to parse *raw_text* as JSON into *schema*. Returns None on failure."""
        if not raw_text or not raw_text.strip():
            return None
        text = _strip_think_blocks(raw_text).strip()
        if not text:
            return None
        # Strip markdown fences if the model wrapped the JSON.
        if text.startswith("```"):
            lines = text.splitlines()
            inner = "\n".join(
                line for line in lines[1:]
                if not line.strip().startswith("```")
            )
            text = _strip_think_blocks(inner).strip()
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
