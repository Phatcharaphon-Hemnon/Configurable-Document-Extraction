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
    # Classified-recovery provenance (backward-compatible defaults).
    # `diagnosis` is the final parse diagnosis when recovery ran;
    # `normalization` records a table-root adaptation (never silent);
    # `finish_reason` is the provider termination string when available.
    diagnosis: str | None = None
    normalization: str | None = None
    finish_reason: str | None = None
    # Effective reasoning control after fallback (None = feature inactive for
    # this call; "low"/"medium"/"high" = sent; "" = removed after an explicit
    # unsupported-parameter rejection).
    reasoning_effort: str | None = None


# Exact (endpoint, model) pairs verified by live probe to accept the
# documented `reasoning_effort` chat-completions parameter. Deliberately NOT
# a substring match: a pair is added here only after a bounded diagnostic
# confirms acceptance AND measures the effect on the verified pair.
#
# Verified 2026-09-14 (docs/reports/nvidia_extractor_timeout_2026-09-14.md):
# ("https://integrate.api.nvidia.com/v1", "openai/gpt-oss-20b") accepted
# reasoning_effort="low" with no 400 and no fallback removal. The env default
# is still "" (feature inactive) — this entry only permits explicit opt-in.
REASONING_EFFORT_ALLOWLIST: frozenset[tuple[str, str]] = frozenset({
    ("https://integrate.api.nvidia.com/v1", "openai/gpt-oss-20b"),
})


# Parse-failure classification for honest recovery (never "invalid JSON" alone).
# - empty: no content at all
# - syntax_error: JSON decoding failed (unambiguous wrappers already stripped)
# - wrong_root: valid JSON but wrong top-level shape (e.g. table as root)
# - table_root: wrong_root subtype — an unambiguous, complete, valid table
#   object returned as the root (preservable via explicit normalization)
# - field_error: right root shape but Pydantic field validation failed
# - truncated: incomplete JSON with termination metadata at the token limit
ParseFailureKind = str


@dataclass(slots=True)
class ParseDiagnosis:
    kind: ParseFailureKind
    message: str
    locations: list[str] | None = None
    raw_length: int = 0
    truncated: bool = False


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
    "nvidia": "https://build.nvidia.com",
    "openclaw": "",
    "opencode": "https://opencode.ai/zen",
    "xkiro": "https://xkiro.com/dashboard",
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
    base = (
        "\n\nIMPORTANT: You MUST respond with a single valid JSON object that "
        "strictly conforms to the following JSON Schema. Do NOT include any "
        "markdown fences, commentary, or extra text — only the raw JSON object.\n\n"
        f"Schema:\n{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}"
    )
    # Extraction contract: one root object with fields+tables. A table object
    # ({name,columns,rows}) belongs INSIDE "tables" and must never be the root.
    # Compact structural example (no reference answers; scalar vs cell kept
    # distinct). Avoids the observed qwen2.5:3b failure that returned a bare
    # {"name":"line_items","columns":[...]} table as the root response.
    if model.__name__ == "ExtractionResponseSchema":
        base += (
            "\n\nOutput contract: return ONE root object {\"fields\":[...],\"tables\":[...]}. "
            "Each table object {\"name\",\"columns\":[{\"key\",\"label\"}],"
            "\"rows\":[[{\"column\",\"value\",\"confidence\",\"source_span\"}]]} "
            "belongs inside \"tables\" — never as the root response. "
            "Scalar fields and table cells stay separate; every value needs its "
            "own verbatim source_span quote. "
            "Example shape (structure only): "
            "{\"fields\":[{\"name\":\"...\",\"value\":\"...\",\"confidence\":0.9,"
            "\"source_span\":\"...\"}],\"tables\":[{\"name\":\"line_items\","
            "\"columns\":[{\"key\":\"...\",\"label\":\"...\"}],"
            "\"rows\":[[{\"column\":\"...\",\"value\":\"...\",\"confidence\":0.9,"
            "\"source_span\":\"...\"}]]}]}"
        )
    return base


def _strip_local_wrappers(raw_text: str | None) -> str:
    """Apply only unambiguous formatting wrappers locally (no invention).

    Handles reasoning <think> blocks and a single markdown fence. Returns the
    stripped text (possibly still invalid). Ambiguous multiple objects and
    incomplete JSON are NOT repaired here — they are classified for recovery.
    """
    if not raw_text or not raw_text.strip():
        return ""
    text = _strip_think_blocks(raw_text).strip()
    if not text:
        return ""
    if text.startswith("```"):
        lines = text.splitlines()
        inner = "\n".join(line for line in lines[1:] if not line.strip().startswith("```"))
        text = _strip_think_blocks(inner).strip()
    return text


def _extract_finish_reason(raw_response: Any) -> str | None:
    """Best-effort finish_reason from a stored raw response (dict or SDK dump)."""
    try:
        if isinstance(raw_response, dict):
            choices = raw_response.get("choices") or []
            if choices and isinstance(choices[0], dict):
                msg = choices[0].get("finish_reason") or choices[0].get("finishReason")
                if isinstance(msg, str) and msg:
                    return msg
                # OpenAI SDK dump nests under message? No — finish_reason is sibling.
                finish = choices[0].get("finish_reason")
                if isinstance(finish, str):
                    return finish
        # MagicMock dumps in tests return {"id": "test"} — no finish reason.
        return None
    except Exception:
        return None


def _validation_locations(exc: Exception) -> list[str]:
    """Concise Pydantic validation locations (e.g. 'fields.0.source_span')."""
    locs: list[str] = []
    try:
        from pydantic import ValidationError

        if isinstance(exc, ValidationError):
            for err in exc.errors()[:8]:
                loc = ".".join(str(p) for p in err.get("loc", ()))
                typ = err.get("type", "")
                msg = err.get("msg", "")[:120]
                locs.append(f"{loc} [{typ}]: {msg}" if loc else f"[{typ}]: {msg}")
    except Exception:
        pass
    return locs


def _is_complete_table_object(obj: Any) -> bool:
    """True for an unambiguous, complete, valid table object (no invention)."""
    if not isinstance(obj, dict):
        return False
    if set(obj.keys()) != {"name", "columns", "rows"}:
        # Allow exactly the table keys; extra/missing keys are not unambiguous.
        # A root with "fields" is not a table root (handled as field_error).
        if not {"name", "columns", "rows"} <= set(obj.keys()):
            return False
        if "fields" in obj or "tables" in obj:
            return False
    try:
        # Local import to avoid cycles at module load.
        from app.schemas.documents import ExtractedTable

        ExtractedTable.model_validate(obj)
        return True
    except Exception:
        return False


def _classify_parse_failure(
    schema: type[BaseModel],
    raw_text: str | None,
    raw_response: Any = None,
) -> ParseDiagnosis:
    """Separate JSON decoding from Pydantic validation with structured output.

    Never invents missing quotes/values/evidence/rows. Incomplete JSON and
    ambiguous multiple objects are rejected as-is for classified recovery.
    """
    raw_len = len(raw_text or "")
    if not raw_text or not raw_text.strip():
        return ParseDiagnosis(
            kind="empty", message="empty output: model returned no content", raw_length=raw_len
        )
    text = _strip_local_wrappers(raw_text)
    if not text:
        return ParseDiagnosis(
            kind="empty",
            message="empty output after stripping unambiguous wrappers",
            raw_length=raw_len,
        )
    # Ambiguous multiple top-level objects: reject, do not pick one silently.
    # A single {...} with surrounding prose is handled by _try_parse; two
    # disjoint {...}{...} blocks are ambiguous.
    try:
        import json as _json

        decoder = _json.JSONDecoder()
        first, idx = decoder.raw_decode(text)
        rest = text[idx:].strip()
        if rest:
            # Trailing content beyond one JSON value: only unambiguous when it
            # is empty/whitespace. Anything else (second object, prose with
            # braces) is ambiguous — reject without invention.
            # Exception: a single closing fence remnant already stripped above.
            return ParseDiagnosis(
                kind="syntax_error",
                message=f"ambiguous multiple JSON values or trailing content ({len(rest)} chars after first value); not repaired locally",
                raw_length=raw_len,
            )
        # Exactly one JSON value decoded — validate against the schema.
        try:
            schema.model_validate(first)
            # Should not happen (caller found parse failure), but treat as OK.
            return ParseDiagnosis(kind="field_error", message="unexpected validation pass", raw_length=raw_len)
        except Exception as vexc:
            locs = _validation_locations(vexc)
            # Table-as-root subtype for the extraction contract.
            if schema.__name__ == "ExtractionResponseSchema" and _is_complete_table_object(first):
                return ParseDiagnosis(
                    kind="table_root",
                    message="valid JSON table object returned as the root; expected {fields,tables} root",
                    locations=locs or ["root: table object must be inside tables"],
                    raw_length=raw_len,
                )
            # Wrong top-level shape vs field-level failure.
            if isinstance(first, dict) and schema.__name__ == "ExtractionResponseSchema":
                if "fields" not in first:
                    return ParseDiagnosis(
                        kind="wrong_root",
                        message="valid JSON with wrong top-level shape: missing required 'fields' (table as root or unrelated object)",
                        locations=locs,
                        raw_length=raw_len,
                    )
            return ParseDiagnosis(
                kind="field_error",
                message="valid JSON root but field-level schema validation failed",
                locations=locs,
                raw_length=raw_len,
            )
    except _json.JSONDecodeError as jexc:
        # Incomplete JSON (truncation) vs genuine syntax error.
        finish = _extract_finish_reason(raw_response)
        looks_truncated = False
        try:
            open_braces = text.count("{") - text.count("}")
            open_brackets = text.count("[") - text.count("]")
            if (open_braces > 0 or open_brackets > 0) and not text.rstrip().endswith(("}", "]", '"')):
                looks_truncated = True
        except Exception:
            pass
        if finish in ("length", "max_tokens", "truncated") or (looks_truncated and finish is None and raw_len > 500):
            # Only claim confirmed truncation when provider metadata says so;
            # otherwise mark as suspected (display "[truncated]" preview marker
            # alone is never evidence of model truncation).
            confirmed = finish in ("length", "max_tokens", "truncated")
            return ParseDiagnosis(
                kind="truncated" if confirmed else "syntax_error",
                message=(
                    f"{'confirmed output truncation' if confirmed else 'possible truncation / JSON syntax error'} "
                    f"(finish_reason={finish!r}, len={raw_len}, decode error: {jexc.msg} at pos {jexc.pos})"
                ),
                raw_length=raw_len,
                truncated=confirmed,
            )
        return ParseDiagnosis(
            kind="syntax_error",
            message=f"JSON syntax error: {jexc.msg} at pos {jexc.pos}",
            raw_length=raw_len,
        )
    except Exception as exc:  # noqa: BLE001 — classification never raises.
        return ParseDiagnosis(
            kind="syntax_error", message=f"parse classification failed: {exc}", raw_length=raw_len
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

    HTTP clients are reused per (endpoint, credentials): AsyncOpenAI holds a
    connection pool, so sharing avoids per-call socket churn while the SDK
    owns lifecycle (no explicit close; process exit reclaims). Only the
    endpoint/model identity enters cache fingerprints — never credentials.
    """

    # Shared transports: (base_url, api_key) -> AsyncOpenAI. In-memory only.
    _shared_clients: dict[tuple[str, str], Any] = {}
    # Provider-confirmed unsupported output tiers: (base_url, model, tier).
    # Recorded ONLY on explicit 400-level "unsupported parameter" responses —
    # never on malformed model output (which retries the same tier).
    _unsupported_tiers: set[tuple[str, str, str]] = set()

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
        self._endpoint = base_url.rstrip("/")
        if not isinstance(AsyncOpenAI, type):
            # Test double patched in (patch AsyncOpenAI): never share across
            # tests — each construction gets the current mock instance.
            self._client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=self._timeout,
                max_retries=0,
            )
            return
        cache_key = (self._endpoint, api_key, float(self._timeout or 0))
        client = Client._shared_clients.get(cache_key)
        if client is None:
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=self._timeout,
                max_retries=0,  # one shared generation budget owns retries
            )
            Client._shared_clients[cache_key] = client
        self._client = client

    def _tier_unsupported(self, model: str, tier: str) -> bool:
        return (getattr(self, "_endpoint", ""), model, tier) in Client._unsupported_tiers

    def _mark_tier_unsupported(self, model: str, tier: str) -> None:
        Client._unsupported_tiers.add((getattr(self, "_endpoint", ""), model, tier))

    # Reasoning-parameter rejections, remembered separately from output-format
    # tiers: only an explicit unsupported-parameter response removes the
    # documented `reasoning_effort` parameter, never an output-format change.
    _reasoning_unsupported: set[tuple[str, str]] = set()

    def _resolve_reasoning_effort(self, model: str, explicit: str | None = None) -> str:
        """Effective reasoning_effort for this call ("" = feature inactive).

        An explicit method-level value (diagnostics) takes responsibility
        itself; the `LLM_REASONING_EFFORT` env default applies only to exact
        (endpoint, model) pairs in REASONING_EFFORT_ALLOWLIST. Everything
        else returns "" — current behavior, byte-identical requests.

        Limitation: "none" is a per-model off-switch, not a universal one,
        and an accepted request does NOT prove reasoning was disabled —
        some providers silently adjust unsupported levels instead of
        rejecting them. Verify effect via output tokens + latency, never via
        acceptance alone.
        """
        if isinstance(explicit, str) and explicit.strip().lower() in ("low", "medium", "high", "none"):
            return explicit.strip().lower()
        env = str(getattr(self.settings, "llm_reasoning_effort", "") or "").strip().lower()
        if env in ("low", "medium", "high", "none") and (
            getattr(self, "_endpoint", ""), model
        ) in REASONING_EFFORT_ALLOWLIST:
            return env
        return ""

    def _reasoning_rejected(self, model: str) -> bool:
        return (getattr(self, "_endpoint", ""), model) in Client._reasoning_unsupported

    def _mark_reasoning_rejected(self, model: str) -> None:
        Client._reasoning_unsupported.add((getattr(self, "_endpoint", ""), model))

    def _effective_reasoning_after_fallback(self, model: str, requested: str) -> str | None:
        """Requested setting minus an explicit rejection (None = inactive)."""
        if not requested:
            return None
        if self._reasoning_rejected(model):
            return ""
        return requested

    @limited_generation
    async def _chat_with_retry(self, **kwargs: Any):
        """Retry transient failures within the generation's shared HTTP budget.

        Logs model, attempt number, request duration, and timeout category
        only — never document content or credentials (kwargs bodies stay
        out of logs; provider details are redacted).
        """
        budget = request_budget.get()
        assert budget is not None
        # Model identity for logs only (never credentials or prompt bodies).
        model_name = str(kwargs.get("model", "")) or "unknown"
        started = asyncio.get_running_loop().time()
        while budget.attempts < RATE_LIMIT_MAX_RETRIES:
            budget.attempts += 1
            self.last_attempts = budget.attempts
            attempt_started = asyncio.get_running_loop().time()
            logger.info(
                "LLM request job=%s stage=%s model=%s attempt=%d/%d elapsed=%.1fs timeout=%.0fs",
                job_context.get(), stage_context.get(), model_name, budget.attempts,
                RATE_LIMIT_MAX_RETRIES, asyncio.get_running_loop().time() - started,
                self._timeout,
            )
            try:
                response = await asyncio.wait_for(
                    self._client.chat.completions.create(**kwargs), timeout=self._timeout,
                )
                duration = asyncio.get_running_loop().time() - attempt_started
                logger.info(
                    "LLM response job=%s stage=%s model=%s attempt=%d duration=%.1fs",
                    job_context.get(), stage_context.get(), model_name,
                    budget.attempts, duration,
                )
                return response
            except Exception as exc:
                duration = asyncio.get_running_loop().time() - attempt_started
                if _is_payment_error(exc):
                    logger.error(
                        "LLM payment error job=%s stage=%s model=%s attempt=%d duration=%.1fs category=payment details=%s",
                        job_context.get(), stage_context.get(), model_name,
                        budget.attempts, duration, extract_provider_error(exc),
                    )
                    raise
                if isinstance(exc, (asyncio.TimeoutError, APITimeoutError)):
                    timeout_category = "timeout"
                    if budget.timeout_retries >= TIMEOUT_MAX_RETRIES:
                        logger.warning(
                            "LLM timeout exhausted job=%s stage=%s model=%s attempt=%d duration=%.1fs category=%s timeout_retries=%d",
                            job_context.get(), stage_context.get(), model_name,
                            budget.attempts, duration, timeout_category,
                            budget.timeout_retries,
                        )
                        raise asyncio.TimeoutError() from exc
                    budget.timeout_retries += 1
                    delay = TIMEOUT_RETRY_BACKOFF_SECONDS
                elif _is_rate_limit_error(exc):
                    timeout_category = "rate_limit"
                    delay = RATE_LIMIT_BACKOFF_SECONDS * 2 ** (budget.attempts - 1)
                    delay += random.uniform(0, 0.5)
                    retry_after = _retry_after_seconds(exc)
                    if retry_after is not None:
                        delay = max(delay, retry_after)
                else:
                    raise
                if budget.attempts >= RATE_LIMIT_MAX_RETRIES:
                    logger.warning(
                        "LLM budget exhausted job=%s stage=%s model=%s attempt=%d duration=%.1fs category=%s",
                        job_context.get(), stage_context.get(), model_name,
                        budget.attempts, duration, timeout_category,
                    )
                    raise
                deadline = stage_deadline.get()
                if deadline is not None and asyncio.get_running_loop().time() + delay >= deadline:
                    logger.warning(
                        "LLM retry skipped job=%s stage=%s model=%s attempt=%d duration=%.1fs category=%s reason=stage_deadline delay=%.1fs",
                        job_context.get(), stage_context.get(), model_name,
                        budget.attempts, duration, timeout_category, delay,
                    )
                    raise ClientError("Provider retry delay exceeds remaining stage deadline") from exc
                logger.warning(
                    "LLM retry job=%s stage=%s model=%s attempt=%d/%d duration=%.1fs category=%s reason=%s delay=%.1fs details=%s",
                    job_context.get(), stage_context.get(), model_name, budget.attempts,
                    RATE_LIMIT_MAX_RETRIES, duration, timeout_category,
                    type(exc).__name__, delay,
                    extract_provider_error(exc),
                )
                await asyncio.sleep(delay)
        raise ClientError("LLM request budget exhausted (4 HTTP attempts)")

    async def _request_mode(self, kwargs: dict[str, Any], request_summary: dict[str, Any]):
        """Only explicit parameter incompatibility permits changing a request."""
        import time as _time

        _mode_started = _time.perf_counter()
        try:
            return await self._chat_with_retry(**kwargs)
        except (asyncio.TimeoutError, APITimeoutError) as exc:
            # Final timeout surface: log duration/attempt/model/category only.
            # request_summary (prompt bodies) and credentials stay out of logs.
            try:
                _budget = request_budget.get()
                _attempts = _budget.attempts if _budget is not None else 0
            except Exception:
                _attempts = 0
            logger.warning(
                "LLM request timed out job=%s stage=%s model=%s attempts=%d duration=%.1fs category=timeout timeout=%.0fs",
                job_context.get(), stage_context.get(), str(kwargs.get("model", "")),
                _attempts, _time.perf_counter() - _mode_started, self._timeout,
            )
            raise ClientError(
                "LLM request timed out",
                request_summary=request_summary,
                provider_details={**extract_provider_error(exc), "timeout": True},
            ) from exc
        except Exception as exc:
            self._fail_fast_if_payment_error(exc, request_summary)
            if "reasoning_effort" in kwargs and _unsupported_parameter(
                exc, ("reasoning_effort", "reasoning")
            ):
                # Explicit parameter rejection ONLY: drop the documented
                # reasoning parameter within the same HTTP budget. The output
                # format tier is untouched (no downgrade); the rejection is
                # remembered so later calls skip the parameter without burning
                # an attempt. Effective setting after fallback: none.
                try:
                    self._mark_reasoning_rejected(str(kwargs.get("model", "")))
                except Exception:
                    pass
                logger.warning(
                    "LLM reasoning_effort rejected job=%s stage=%s model=%s "
                    "— param removed, format unchanged, effective=none",
                    job_context.get(), stage_context.get(), str(kwargs.get("model", "")),
                )
                kwargs = {key: value for key, value in kwargs.items() if key != "reasoning_effort"}
                return await self._request_mode(kwargs, request_summary)
            if "extra_body" in kwargs and _unsupported_parameter(exc, ("reasoning", "extra_body")):
                # Retry only an explicitly rejected optional parameter.
                kwargs = {key: value for key, value in kwargs.items() if key != "extra_body"}
                return await self._request_mode(kwargs, request_summary)
            if "response_format" in kwargs and _unsupported_parameter(
                exc, ("response_format", "json_schema", "json_object")
            ):
                # Remember provider-confirmed unsupported tiers so later calls
                # skip them without burning a request. Malformed model output
                # never marks a tier unsupported (it retries the same tier).
                try:
                    fmt = kwargs["response_format"]
                    tier = "json_schema" if isinstance(fmt, dict) and fmt.get("type") == "json_schema" else "json_object"
                    self._mark_tier_unsupported(str(kwargs.get("model", "")), tier)
                except Exception:
                    pass
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

    def _strongest_tier(self, model: str) -> str | None:
        """Strongest supported output tier (None only when both structured tiers rejected)."""
        if not getattr(self.settings, "disable_strict_json_schema", False) and not self._tier_unsupported(
            model, "json_schema"
        ):
            return "json_schema"
        if not self._tier_unsupported(model, "json_object"):
            return "json_object"
        return None

    def _schema_fingerprint(self, response_schema: type[BaseModel]) -> str:
        """Short hash of the serialized strict schema (for failure reports)."""
        try:
            import hashlib

            canonical = json.dumps(
                _pydantic_to_json_schema(response_schema), sort_keys=True, ensure_ascii=False, default=str
            )
            return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
        except Exception:
            return "unknown"

    async def _call_tier(
        self,
        tier: str,
        *,
        model: str,
        messages: list[dict[str, Any]],
        response_schema: type[T],
        temperature: float,
        request_summary: dict[str, Any],
        max_tokens: int | None,
        disable_reasoning: bool,
        reasoning_effort: str = "",
    ):
        if tier == "json_schema":
            return await self._call_with_schema(
                model=model, messages=messages, response_schema=response_schema,
                temperature=temperature, request_summary=request_summary,
                max_tokens=max_tokens, disable_reasoning=disable_reasoning,
                reasoning_effort=reasoning_effort,
            )
        if tier == "json_object":
            return await self._call_with_json_object_mode(
                model=model, messages=messages, temperature=temperature,
                request_summary=request_summary, max_tokens=max_tokens,
                disable_reasoning=disable_reasoning,
                reasoning_effort=reasoning_effort,
            )
        return await self._call_plain(
            model=model, messages=messages, temperature=temperature,
            request_summary=request_summary, max_tokens=max_tokens,
            disable_reasoning=disable_reasoning,
            reasoning_effort=reasoning_effort,
        )

    def _classified_error(
        self,
        *,
        response_schema: type[T],
        model: str,
        tier: str | None,
        raw_text: str | None,
        raw_response: Any,
        diagnosis: ParseDiagnosis | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        total_tokens: int | None,
        elapsed: float,
        max_tokens: int | None,
        request_summary: dict[str, Any],
        attempts_made: int,
    ) -> ClientError:
        finish = _extract_finish_reason(raw_response)
        raw_len = len(raw_text or "")
        kind = diagnosis.kind if diagnosis else "unknown"
        locs = "; ".join(diagnosis.locations or []) if diagnosis and diagnosis.locations else ""
        detail = diagnosis.message if diagnosis else "no diagnosis"
        # Distinguish display-only preview truncation from model truncation:
        # the "[truncated, N chars]" marker below is display-only.
        preview = _truncate(raw_text or "(empty response)", 300)
        msg = (
            f"LLM output failed ({kind}) for schema {response_schema.__name__} "
            f"(model={model}, format={tier or 'none'}, schema_fp={self._schema_fingerprint(response_schema)}, "
            f"max_tokens={max_tokens}, finish_reason={finish!r}, len={raw_len}, "
            f"tokens={prompt_tokens}/{completion_tokens}/{total_tokens}, "
            f"elapsed={elapsed:.1f}s, model_calls={attempts_made}). "
            f"{detail}"
            + (f" Validation: {locs}" if locs else "")
            + f" Raw response preview: {preview} (display-only truncation; full len={raw_len})"
        )
        return ClientError(msg, request_summary=request_summary, raw_response=raw_text)

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
        reasoning_effort: str | None = None,
    ) -> ClientResult:
        """Call the model and parse the response into *response_schema*.

        Classified recovery (no blind regeneration): one initial generation in
        the strongest supported format, then at most ONE corrective generation
        for parse/schema failures within the shared HTTP budget. Transport
        retries share the same budget via _chat_with_retry but are separate
        from content correction. Plain-text downgrade happens only when both
        structured tiers are explicitly unsupported — never because schema
        validation failed.

        `reasoning_effort` ("low"/"medium"/"high"/"none", default None) is an
        explicit per-call override for endpoints with a documented parameter;
        otherwise the `LLM_REASONING_EFFORT` env default applies solely to
        exact (endpoint, model) pairs in REASONING_EFFORT_ALLOWLIST. The
        effective setting after fallback is recorded on the returned
        ClientResult (None = feature inactive).
        """
        import time as _time

        temperature = _resolve_temperature(self.settings, temperature)
        reasoning_effort = self._resolve_reasoning_effort(model, explicit=reasoning_effort)
        request_summary: dict[str, Any] = {
            "model": model,
            "prompt": _truncate(prompt),
            "response_schema": response_schema.__name__,
            "temperature": temperature,
            "disable_reasoning": disable_reasoning,
            "reasoning_effort": reasoning_effort or None,
        }

        messages = [{"role": "user", "content": prompt + _build_json_prompt_suffix(response_schema)}]
        endpoint = getattr(self, "_endpoint", "")
        logger.info(
            "LLM structured start job=%s stage=%s endpoint=%s model=%s schema=%s schema_fp=%s format=%s max_tokens=%s temp=%s reasoning_effort=%s",
            job_context.get(), stage_context.get(), endpoint, model, response_schema.__name__,
            self._schema_fingerprint(response_schema),
            self._strongest_tier(model), max_tokens, temperature,
            reasoning_effort or "none",
        )

        # --- Attempt 1: strongest supported tier ---
        tier = self._strongest_tier(model)
        if tier is None:
            # Both structured tiers explicitly rejected by the provider.
            logger.info(
                "No structured tier supported for model=%s (json_schema + json_object rejected); using plain once",
                model,
            )
            tier = "plain"
        elif tier == "json_schema" and getattr(self.settings, "disable_strict_json_schema", False):
            logger.info("DISABLE_STRICT_JSON_SCHEMA=true — starting at json_object for model=%s", model)
        else:
            logger.info("Attempt 1 (%s): model=%s schema=%s", tier, model, response_schema.__name__)

        t0 = _time.perf_counter()
        raw_text, raw_response, prompt_tokens, completion_tokens, total_tokens = await self._call_tier(
            tier, model=model, messages=messages, response_schema=response_schema,
            temperature=temperature, request_summary=request_summary,
            max_tokens=max_tokens, disable_reasoning=disable_reasoning,
            reasoning_effort=reasoning_effort,
        )
        elapsed1 = _time.perf_counter() - t0
        attempts_made = 1
        # Provider explicitly rejected the tier (returned None): try the next
        # supported tier once WITHOUT counting it as a content-correction retry.
        # Malformed model output never marks a tier unsupported (handled in
        # _request_mode) and never triggers a tier downgrade here.
        if raw_text is None and raw_response is None:
            logger.warning(
                "LLM tier %s explicitly unsupported for model=%s (provider rejection); trying next tier once",
                tier, model,
            )
            fallback = "json_object" if tier == "json_schema" else "plain"
            if fallback == "json_object" and self._tier_unsupported(model, "json_object"):
                fallback = "plain"
            if fallback != tier:
                tier = fallback
                logger.info("Attempt 1b (%s): model=%s schema=%s (after explicit rejection)", tier, model, response_schema.__name__)
                t0 = _time.perf_counter()
                raw_text, raw_response, prompt_tokens, completion_tokens, total_tokens = await self._call_tier(
                    tier, model=model, messages=messages, response_schema=response_schema,
                    temperature=temperature, request_summary=request_summary,
                    max_tokens=max_tokens, disable_reasoning=disable_reasoning,
                    reasoning_effort=reasoning_effort,
                )
                elapsed1 = _time.perf_counter() - t0
            else:
                elapsed1 = 0.0

        parsed, diagnosis = self._try_parse_detailed(response_schema, raw_text, raw_response)
        finish = _extract_finish_reason(raw_response)
        logger.info(
            "LLM attempt 1 done job=%s stage=%s tier=%s len=%s finish=%r tokens=%s/%s/%s elapsed=%.1fs parsed=%s kind=%s",
            job_context.get(), stage_context.get(), tier,
            len(raw_text or "") if raw_text else 0, finish,
            prompt_tokens, completion_tokens, total_tokens, elapsed1,
            parsed is not None, diagnosis.kind if diagnosis else "ok",
        )
        if reasoning_effort:
            logger.info(
                "LLM reasoning effective job=%s stage=%s model=%s requested=%s effective=%s",
                job_context.get(), stage_context.get(), model, reasoning_effort,
                self._effective_reasoning_after_fallback(model, reasoning_effort) or "none",
            )
        if parsed is not None:
            self._record_usage(prompt_tokens, completion_tokens, total_tokens)
            return ClientResult(
                parsed=parsed, raw_text=raw_text, raw_response=raw_response,
                request_summary=request_summary, prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens, total_tokens=total_tokens,
                diagnosis="ok", finish_reason=finish,
                reasoning_effort=self._effective_reasoning_after_fallback(model, reasoning_effort),
            )

        # --- Classified recovery: at most ONE corrective generation ---
        assert diagnosis is not None
        if diagnosis.kind == "truncated" and diagnosis.truncated:
            # Confirmed token-limit truncation: do NOT blindly retry the same
            # oversized request or accept the partial response. Report honestly
            # with output size, limits, and termination metadata for the caller
            # to compact/split by source regions (see task §5).
            logger.error(
                "LLM confirmed truncation job=%s stage=%s schema=%s len=%d max_tokens=%s finish=%r tokens=%s/%s elapsed=%.1fs",
                job_context.get(), stage_context.get(), response_schema.__name__,
                len(raw_text or ""), max_tokens, finish, prompt_tokens, completion_tokens, elapsed1,
            )
            raise self._classified_error(
                response_schema=response_schema, model=model, tier=tier, raw_text=raw_text,
                raw_response=raw_response, diagnosis=diagnosis, prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens, total_tokens=total_tokens,
                elapsed=elapsed1, max_tokens=max_tokens, request_summary=request_summary,
                attempts_made=attempts_made,
            )

        # Table-as-root: preserve the unambiguous table, record normalization,
        # and use the single corrective budget for a targeted scalar recovery.
        # Never silently claim scalar extraction completed via fields=[].
        table_adapted = None
        normalization_note = None
        if diagnosis.kind == "table_root":
            table_adapted = self.adapt_table_root(raw_text)
            if table_adapted is not None:
                normalization_note = (
                    "normalized table-as-root to {fields:[], tables:[table]}; "
                    "scalar extraction incomplete (fields=[] is not a success claim; "
                    "coverage/needs_review will flag missing scalars)"
                )
                logger.warning(
                    "LLM table-as-root job=%s stage=%s schema=%s — %s",
                    job_context.get(), stage_context.get(), response_schema.__name__, normalization_note,
                )

        # One corrective generation in the STRONGEST supported format (never an
        # automatic plain downgrade for schema failures). Includes concise
        # validation locations + required structure + original source evidence;
        # the previous answer is treated as untrusted data (not included).
        locs = "; ".join((diagnosis.locations or [])[:5]) if diagnosis.locations else diagnosis.message[:300]
        corrective_instruction = (
            "The previous response failed validation and is UNTRUSTED — do not copy it. "
            f"Validation: {locs}. "
            "Return ONE root JSON object matching the Schema above "
        )
        if response_schema.__name__ == "ExtractionResponseSchema":
            corrective_instruction += (
                'with {\"fields\":[...],\"tables\":[...]} — a table object belongs inside '
                '\"tables\", never as the root. '
            )
        corrective_instruction += (
            "Use only the original source data from the first message. "
            "Return only raw JSON, no fences or commentary."
        )
        corrective_messages = [*messages, {"role": "user", "content": corrective_instruction}]
        logger.warning(
            "LLM corrective (1 allowed) job=%s stage=%s schema=%s kind=%s tier=%s — %s",
            job_context.get(), stage_context.get(), response_schema.__name__,
            diagnosis.kind, tier, locs[:300],
        )
        t1 = _time.perf_counter()
        c_raw, c_resp, c_pt, c_ct, c_tt = await self._call_tier(
            tier, model=model, messages=corrective_messages, response_schema=response_schema,
            temperature=temperature, request_summary=request_summary,
            max_tokens=max_tokens, disable_reasoning=disable_reasoning,
            reasoning_effort=reasoning_effort,
        )
        elapsed2 = _time.perf_counter() - t1
        attempts_made += 1
        c_parsed, c_diag = self._try_parse_detailed(response_schema, c_raw, c_resp)
        c_finish = _extract_finish_reason(c_resp)
        logger.info(
            "LLM corrective done job=%s stage=%s tier=%s len=%s finish=%r tokens=%s/%s elapsed=%.1fs parsed=%s kind=%s total_calls=%d",
            job_context.get(), stage_context.get(), tier,
            len(c_raw or "") if c_raw else 0, c_finish, c_pt, c_ct, elapsed2,
            c_parsed is not None, c_diag.kind if c_diag else "ok", attempts_made,
        )
        if c_parsed is not None:
            # Preserve valid data without treating partial recovery as complete
            # success: downstream coverage/acceptance still judges completeness.
            self._record_usage(c_pt, c_ct, c_tt)
            return ClientResult(
                parsed=c_parsed, raw_text=c_raw, raw_response=c_resp,
                request_summary=request_summary, prompt_tokens=c_pt,
                completion_tokens=c_ct, total_tokens=c_tt,
                diagnosis=f"corrective-{diagnosis.kind}", normalization=normalization_note,
                finish_reason=c_finish,
                reasoning_effort=self._effective_reasoning_after_fallback(model, reasoning_effort),
            )
        # Corrective failed: if we preserved an unambiguous table, return it
        # explicitly as partial (fields=[] incomplete) rather than inventing
        # scalars or forcing another regeneration. Otherwise raise honestly.
        if table_adapted is not None:
            logger.warning(
                "LLM corrective failed job=%s stage=%s — returning preserved table-as-root partial "
                "(fields=[] incomplete, needs_review downstream); corrective kind=%s",
                job_context.get(), stage_context.get(), c_diag.kind if c_diag else "unknown",
            )
            self._record_usage(c_pt, c_ct, c_tt)
            return ClientResult(
                parsed=table_adapted, raw_text=raw_text, raw_response=raw_response,
                request_summary=request_summary, prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens, total_tokens=total_tokens,
                diagnosis=f"table_root-partial({c_diag.kind if c_diag else 'failed'})",
                normalization=normalization_note, finish_reason=finish,
                reasoning_effort=self._effective_reasoning_after_fallback(model, reasoning_effort),
            )
        raise self._classified_error(
            response_schema=response_schema, model=model, tier=tier, raw_text=c_raw or raw_text,
            raw_response=c_resp or raw_response, diagnosis=c_diag or diagnosis,
            prompt_tokens=c_pt or prompt_tokens, completion_tokens=c_ct or completion_tokens,
            total_tokens=c_tt or total_tokens, elapsed=elapsed1 + elapsed2,
            max_tokens=max_tokens, request_summary=request_summary, attempts_made=attempts_made,
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

        # Classified recovery (vision legacy path): strongest tier once, then at
        # most ONE corrective. Logs distinguish skipped tiers from failed ones.
        import time as _vtime

        tier = self._strongest_tier(model)
        attempt1_ran = False
        if tier is None:
            logger.info(
                "No structured tier supported for model=%s (vision); using plain once", model
            )
            tier = "plain"
        else:
            logger.info(
                "Attempt 1 (%s+vision): model=%s schema=%s image_size=%d",
                tier, model, response_schema.__name__, len(image_bytes),
            )
        t0 = _vtime.perf_counter()
        raw_text, raw_response, prompt_tokens, completion_tokens, total_tokens = await self._call_tier(
            tier, model=model, messages=messages, response_schema=response_schema,
            temperature=temperature, request_summary=request_summary,
            max_tokens=max_tokens, disable_reasoning=disable_reasoning,
        )
        elapsed1 = _vtime.perf_counter() - t0
        attempt1_ran = not (raw_text is None and raw_response is None)
        if not attempt1_ran:
            logger.warning(
                "LLM tier %s+vision explicitly unsupported for model=%s; trying next tier once (not a content failure)",
                tier, model,
            )
            fallback = "json_object" if tier == "json_schema" else "plain"
            if fallback == "json_object" and self._tier_unsupported(model, "json_object"):
                fallback = "plain"
            if fallback != tier:
                tier = fallback
                t0 = _vtime.perf_counter()
                raw_text, raw_response, prompt_tokens, completion_tokens, total_tokens = await self._call_tier(
                    tier, model=model, messages=messages, response_schema=response_schema,
                    temperature=temperature, request_summary=request_summary,
                    max_tokens=max_tokens, disable_reasoning=disable_reasoning,
                )
                elapsed1 = _vtime.perf_counter() - t0
                attempt1_ran = not (raw_text is None and raw_response is None)

        parsed, diagnosis = self._try_parse_detailed(response_schema, raw_text, raw_response)
        if parsed is not None:
            self._record_usage(prompt_tokens, completion_tokens, total_tokens)
            return ClientResult(
                parsed=parsed, raw_text=raw_text, raw_response=raw_response,
                request_summary=request_summary, prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens, total_tokens=total_tokens,
                diagnosis="ok", finish_reason=_extract_finish_reason(raw_response),
            )
        assert diagnosis is not None
        if diagnosis.truncated:
            raise self._classified_error(
                response_schema=response_schema, model=model, tier=tier, raw_text=raw_text,
                raw_response=raw_response, diagnosis=diagnosis, prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens, total_tokens=total_tokens,
                elapsed=elapsed1, max_tokens=max_tokens, request_summary=request_summary,
                attempts_made=1,
            )
        table_adapted = self.adapt_table_root(raw_text) if diagnosis.kind == "table_root" else None
        normalization_note = (
            "normalized table-as-root to {fields:[], tables:[table]}; scalar incomplete"
            if table_adapted is not None else None
        )
        locs = "; ".join((diagnosis.locations or [])[:5]) if diagnosis.locations else diagnosis.message[:300]
        # Only log "returned invalid" when attempt 1 actually generated output.
        if attempt1_ran:
            logger.warning(
                "LLM vision corrective (1 allowed) schema=%s kind=%s tier=%s — %s",
                response_schema.__name__, diagnosis.kind, tier, locs[:300],
            )
        else:
            logger.info(
                "LLM vision corrective after skipped tier schema=%s kind=%s tier=%s",
                response_schema.__name__, diagnosis.kind, tier,
            )
        corrective_messages = [*messages, {
            "role": "user",
            "content": (
                "The previous response failed validation and is UNTRUSTED — do not copy it. "
                f"Validation: {locs}. Return ONE root JSON object matching the Schema; "
                "a table object belongs inside \"tables\", never as the root. "
                "Use only the original source data. Return only raw JSON."
            ),
        }]
        t1 = _vtime.perf_counter()
        c_raw, c_resp, c_pt, c_ct, c_tt = await self._call_tier(
            tier, model=model, messages=corrective_messages, response_schema=response_schema,
            temperature=temperature, request_summary=request_summary,
            max_tokens=max_tokens, disable_reasoning=disable_reasoning,
        )
        elapsed2 = _vtime.perf_counter() - t1
        c_parsed, c_diag = self._try_parse_detailed(response_schema, c_raw, c_resp)
        if c_parsed is not None:
            self._record_usage(c_pt, c_ct, c_tt)
            return ClientResult(
                parsed=c_parsed, raw_text=c_raw, raw_response=c_resp,
                request_summary=request_summary, prompt_tokens=c_pt,
                completion_tokens=c_ct, total_tokens=c_tt,
                diagnosis=f"corrective-{diagnosis.kind}", normalization=normalization_note,
                finish_reason=_extract_finish_reason(c_resp),
            )
        if table_adapted is not None:
            self._record_usage(c_pt, c_ct, c_tt)
            return ClientResult(
                parsed=table_adapted, raw_text=raw_text, raw_response=raw_response,
                request_summary=request_summary, prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens, total_tokens=total_tokens,
                diagnosis=f"table_root-partial({c_diag.kind if c_diag else 'failed'})",
                normalization=normalization_note,
                finish_reason=_extract_finish_reason(raw_response),
            )
        raise self._classified_error(
            response_schema=response_schema, model=model, tier=tier, raw_text=c_raw or raw_text,
            raw_response=c_resp or raw_response, diagnosis=c_diag or diagnosis,
            prompt_tokens=c_pt or prompt_tokens, completion_tokens=c_ct or completion_tokens,
            total_tokens=c_tt or total_tokens, elapsed=elapsed1 + elapsed2,
            max_tokens=max_tokens, request_summary=request_summary, attempts_made=2,
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
        reasoning_effort: str = "",
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
        if reasoning_effort and not self._reasoning_rejected(model):
            # Documented reasoning control replaces the undocumented
            # extra_body flag (never send both).
            kwargs["reasoning_effort"] = reasoning_effort
        elif disable_reasoning:
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
        reasoning_effort: str = "",
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
        if reasoning_effort and not self._reasoning_rejected(model):
            # Documented reasoning control replaces the undocumented
            # extra_body flag (never send both).
            kwargs["reasoning_effort"] = reasoning_effort
        elif disable_reasoning:
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
        reasoning_effort: str = "",
    ) -> tuple[str | None, Any, int | None, int | None, int | None]:
        """Plain chat completion call (no response_format param)."""
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if reasoning_effort and not self._reasoning_rejected(model):
            # Documented reasoning control replaces the undocumented
            # extra_body flag (never send both).
            kwargs["reasoning_effort"] = reasoning_effort
        elif disable_reasoning:
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
        """Try to parse *raw_text* as JSON into *schema*. Returns None on failure.

        Handles only unambiguous formatting wrappers locally (think blocks,
        a single markdown fence, surrounding prose with one JSON object).
        Ambiguous multiple objects and incomplete JSON are NOT repaired —
        see _try_parse_detailed for classification.
        """
        parsed, _ = Client._try_parse_detailed(schema, raw_text)
        return parsed

    @staticmethod
    def _try_parse_detailed(
        schema: type[T], raw_text: str | None, raw_response: Any = None
    ) -> tuple[T | None, ParseDiagnosis | None]:
        """Parse with structured diagnostics (JSON vs schema failures separated)."""
        if not raw_text or not raw_text.strip():
            return None, ParseDiagnosis(
                kind="empty", message="empty output", raw_length=len(raw_text or "")
            )
        text = _strip_local_wrappers(raw_text)
        if not text:
            return None, ParseDiagnosis(
                kind="empty",
                message="empty output after stripping unambiguous wrappers",
                raw_length=len(raw_text or ""),
            )
        try:
            parsed = schema.model_validate_json(text)
            return parsed, None
        except Exception:
            pass
        # Single surrounding-prose wrapper: exactly one {...} block. Multiple
        # disjoint objects are ambiguous — reject without picking one.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = text[start : end + 1]
            # Reject when a second top-level object follows the first (e.g.
            # "}{" with non-whitespace between balanced braces).
            try:
                import json as _json

                decoder = _json.JSONDecoder()
                _, idx = decoder.raw_decode(candidate)
                if not candidate[idx:].strip():
                    try:
                        parsed = schema.model_validate_json(candidate)
                        return parsed, None
                    except Exception:
                        pass
            except Exception:
                pass
        diagnosis = _classify_parse_failure(schema, raw_text, raw_response)
        return None, diagnosis

    @staticmethod
    def adapt_table_root(raw_text: str | None) -> Any | None:
        """Return an ExtractionResponseSchema for an unambiguous table-as-root.

        Requires a complete, valid table object (name/columns/rows validating
        as ExtractedTable). Returns None otherwise — never invents quotes,
        values, evidence, or rows. Callers must record the normalization and
        retain incomplete status for missing scalars (coverage/needs_review).
        """
        if not raw_text or not raw_text.strip():
            return None
        text = _strip_local_wrappers(raw_text)
        if not text:
            return None
        try:
            import json as _json

            obj = _json.loads(text)
        except Exception:
            # Prose-wrapped single object (unambiguous only).
            try:
                start = text.find("{")
                end = text.rfind("}")
                if start == -1 or end <= start:
                    return None
                import json as _json2

                obj = _json2.loads(text[start : end + 1])
            except Exception:
                return None
        if not _is_complete_table_object(obj):
            return None
        try:
            from app.schemas.documents import ExtractedTable
            from app.schemas.llm_schemas import ExtractionResponseSchema

            table = ExtractedTable.model_validate(obj)
            return ExtractionResponseSchema(fields=[], tables=[table])
        except Exception:
            return None
