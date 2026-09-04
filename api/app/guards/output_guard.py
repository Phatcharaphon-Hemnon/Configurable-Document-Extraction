"""Output validation guards for LLM responses."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Type, TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class OutputValidationError(Exception):
    """Raised when output validation fails."""

    def __init__(self, message: str, raw_output: str):
        super().__init__(message)
        self.raw_output = raw_output


def clean_llm_output(raw_text: str) -> str:
    """Clean common LLM output formatting issues.

    Handles:
    - Markdown code fences (```json ... ```)
    - Leading/trailing prose before JSON
    - Extra whitespace

    Args:
        raw_text: Raw LLM response text.

    Returns:
        Cleaned text ready for JSON parsing.
    """
    if not raw_text:
        return ""

    text = raw_text.strip()

    # Remove markdown code fences
    if text.startswith("```"):
        lines = text.splitlines()
        inner = "\n".join(
            line for line in lines[1:] if not line.strip().startswith("```")
        )
        text = inner.strip()

    # Remove any leading/trailing prose (find first { or [)
    json_start = -1
    for i, char in enumerate(text):
        if char in ("{", "["):
            json_start = i
            break

    if json_start > 0:
        text = text[json_start:]

    # Find the matching closing bracket
    if text and text[0] in ("{", "["):
        open_char = text[0]
        close_char = "}" if open_char == "{" else "]"
        depth = 0
        end_pos = len(text)

        for i, char in enumerate(text):
            if char == open_char:
                depth += 1
            elif char == close_char:
                depth -= 1
                if depth == 0:
                    end_pos = i + 1
                    break

        text = text[:end_pos]

    return text.strip()


def validate_output_schema(
    raw_text: str,
    schema: Type[T],
    *,
    strict: bool = True,
    fallback: T | None = None,
) -> T:
    """Validate LLM output against a Pydantic schema.

    Args:
        raw_text: Raw LLM response text.
        schema: Pydantic model class to validate against.
        strict: If True, raise on validation failure.
        fallback: Optional fallback instance to return on failure.

    Returns:
        Validated Pydantic model instance.

    Raises:
        OutputValidationError: If validation fails and strict=True.
    """
    cleaned = clean_llm_output(raw_text)

    if not cleaned:
        if strict:
            raise OutputValidationError("Empty LLM output", raw_text)
        return fallback or schema()

    # Try JSON parsing
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        if strict:
            raise OutputValidationError(
                f"Invalid JSON in LLM output: {e}",
                raw_text,
            )
        logger.warning("LLM output JSON parse failed, returning fallback: %s", e)
        return fallback or schema()

    # Validate against schema
    try:
        return schema.model_validate(data)
    except ValidationError as e:
        if strict:
            raise OutputValidationError(
                f"Schema validation failed: {e}",
                raw_text,
            )
        logger.warning("LLM output schema validation failed: %s", e)
        return fallback or schema()


def sanitize_error_message(
    error: Exception,
    *,
    include_details: bool = False,
    hide_internal_paths: bool = True,
) -> str:
    """Sanitize error messages to prevent information leakage.

    Removes internal paths, API keys, hostnames, and other sensitive
    information from error messages before returning to clients.

    Args:
        error: Exception to sanitize.
        include_details: If True, include more technical details.
        hide_internal_paths: If True, replace file paths with [PATH].

    Returns:
        Sanitized error message safe for client display.
    """
    error_str = str(error)

    # Remove file paths
    if hide_internal_paths:
        error_str = re.sub(r"(/[a-zA-Z0-9_.-]+)+", "[PATH]", error_str)

    # Remove API keys
    error_str = re.sub(
        r"(api[_-]?key|secret|token|password)[=:]\s*\S+",
        r"\1=[REDACTED]",
        error_str,
        flags=re.IGNORECASE,
    )

    # Remove hostnames and IPs
    error_str = re.sub(r"localhost:\d+", "[HOST]", error_str)
    error_str = re.sub(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+", "[HOST]", error_str)
    error_str = re.sub(
        r"https?://[^\s]+", "[URL]", error_str
    )

    if not include_details:
        # Generic error messages for clients
        error_lower = error_str.lower()
        if "timeout" in error_lower:
            return "Request timed out. Please try again."
        elif "rate limit" in error_lower:
            return "Too many requests. Please try again later."
        elif "validation" in error_lower:
            return "Invalid request data."
        elif "not found" in error_lower:
            return "Resource not found."
        elif "permission" in error_lower or "unauthorized" in error_lower:
            return "Permission denied."
        elif "file" in error_lower and ("large" in error_lower or "size" in error_lower):
            return "File is too large."
        elif "unsupported" in error_lower and "type" in error_lower:
            return "Unsupported file type."

    return error_str


def validate_llm_response_length(
    text: str,
    max_length: int = 50000,
    *,
    truncate: bool = True,
) -> str:
    """Validate LLM response length.

    Args:
        text: LLM response text.
        max_length: Maximum allowed length.
        truncate: If True, truncate; if False, raise on exceed.

    Returns:
        Validated text.

    Raises:
        OutputValidationError: If truncate=False and text exceeds limit.
    """
    if not text:
        return ""

    if len(text) > max_length:
        if truncate:
            return text[:max_length] + "…[truncated]"
        raise OutputValidationError(
            f"LLM response too long: {len(text):,} chars (max: {max_length:,})",
            text,
        )

    return text


def detect_suspicious_output(
    text: str,
    *,
    max_repeated_tokens: int = 100,
    min_unique_ratio: float = 0.3,
) -> tuple[bool, str | None]:
    """Detect suspicious LLM output patterns.

    Checks for:
    - Excessive token repetition
    - Low unique token ratio
    - Potential prompt leakage

    Args:
        text: LLM response text.
        max_repeated_tokens: Max allowed consecutive repeated tokens.
        min_unique_ratio: Minimum ratio of unique to total tokens.

    Returns:
        Tuple of (is_suspicious, reason).
    """
    if not text:
        return False, None

    # Check for excessive repetition
    tokens = text.split()
    if len(tokens) > max_repeated_tokens:
        # Check for repeated sequences
        for window in [5, 10, 20]:
            if len(tokens) >= window:
                last_tokens = tokens[-window:]
                if len(set(last_tokens)) == 1:
                    return True, f"Excessive token repetition (last {window} tokens identical)"

    # Check unique token ratio
    if len(tokens) > 50:
        unique_ratio = len(set(tokens)) / len(tokens)
        if unique_ratio < min_unique_ratio:
            return True, f"Low unique token ratio: {unique_ratio:.2f}"

    # Check for potential prompt leakage
    leakage_patterns = [
        r"(system|assistant)\s*prompt",
        r"(reveal|show|print)\s+(your|the)\s+(instructions|rules)",
        r"you\s+are\s+(a|an|the)\s+",
        r"ignore\s+(all\s+)?previous",
    ]
    for pattern in leakage_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True, f"Potential prompt leakage detected"

    return False, None
