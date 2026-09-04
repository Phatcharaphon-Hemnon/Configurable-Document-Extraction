"""Content size and validation guards."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field


class ContentLimits(BaseModel):
    """Configurable limits for content processing."""

    # Document text limits
    max_document_chars: int = Field(
        default=50000,
        description="Maximum characters in document text",
    )
    max_prompt_chars: int = Field(
        default=12000,
        description="Maximum characters in LLM prompts",
    )

    # Field value limits
    max_field_name_length: int = Field(
        default=100,
        description="Maximum field name length",
    )
    max_field_value_length: int = Field(
        default=1000,
        description="Maximum field value length",
    )
    max_source_span_length: int = Field(
        default=500,
        description="Maximum source span length",
    )

    # Response limits
    max_extraction_fields: int = Field(
        default=100,
        description="Maximum fields per extraction",
    )
    max_validation_errors: int = Field(
        default=50,
        description="Maximum validation errors",
    )


DEFAULT_LIMITS = ContentLimits()

# Valid field name pattern (snake_case alphanumeric)
_FIELD_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class ContentValidationError(Exception):
    """Raised when content validation fails."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


def validate_document_text(
    text: str,
    limits: ContentLimits | None = None,
    *,
    truncate: bool = True,
) -> str:
    """Validate and optionally truncate document text.

    Args:
        text: Document text to validate.
        limits: Custom limits to apply.
        truncate: If True, truncate text exceeding limits. If False, raise.

    Returns:
        Validated (and possibly truncated) text.

    Raises:
        ContentValidationError: If truncate=False and text exceeds limits.
    """
    limits = limits or DEFAULT_LIMITS

    if len(text) > limits.max_document_chars:
        if truncate:
            return text[: limits.max_document_chars] + "…[truncated]"
        raise ContentValidationError(
            f"Document text too long: {len(text):,} chars "
            f"(max: {limits.max_document_chars:,})",
            field="document_text",
        )

    return text


def validate_prompt_text(
    text: str,
    limits: ContentLimits | None = None,
    *,
    truncate: bool = True,
) -> str:
    """Validate and optionally truncate prompt text.

    Args:
        text: Prompt text to validate.
        limits: Custom limits to apply.
        truncate: If True, truncate text exceeding limits. If False, raise.

    Returns:
        Validated (and possibly truncated) text.

    Raises:
        ContentValidationError: If truncate=False and text exceeds limits.
    """
    limits = limits or DEFAULT_LIMITS

    if len(text) > limits.max_prompt_chars:
        if truncate:
            return text[: limits.max_prompt_chars] + "…[truncated]"
        raise ContentValidationError(
            f"Prompt text too long: {len(text):,} chars "
            f"(max: {limits.max_prompt_chars:,})",
            field="prompt_text",
        )

    return text


def validate_field_name(
    name: str,
    limits: ContentLimits | None = None,
) -> str | None:
    """Validate field name length and format.

    Args:
        name: Field name to validate.
        limits: Custom limits to apply.

    Returns:
        Validated field name, or None if invalid (should be skipped).
    """
    limits = limits or DEFAULT_LIMITS

    if not name or not name.strip():
        return None

    name = name.strip()

    # Check length
    if len(name) > limits.max_field_name_length:
        return None

    # Only allow snake_case alphanumeric
    if not _FIELD_NAME_PATTERN.match(name):
        return None

    return name


def validate_field_value(
    value: str | float | None,
    limits: ContentLimits | None = None,
    *,
    truncate: bool = True,
) -> str | float | None:
    """Validate field value length.

    Args:
        value: Field value to validate.
        limits: Custom limits to apply.
        truncate: If True, truncate values exceeding limits.

    Returns:
        Validated (and possibly truncated) value, or None.
    """
    limits = limits or DEFAULT_LIMITS

    if value is None:
        return None

    if isinstance(value, str):
        if len(value) > limits.max_field_value_length:
            if truncate:
                return value[: limits.max_field_value_length] + "…[truncated]"
            raise ContentValidationError(
                f"Field value too long: {len(value):,} chars "
                f"(max: {limits.max_field_value_length:,})",
            )
        return value

    return value


def validate_source_span(
    span: str | None,
    limits: ContentLimits | None = None,
    *,
    truncate: bool = True,
) -> str | None:
    """Validate source span length.

    Args:
        span: Source span to validate.
        limits: Custom limits to apply.
        truncate: If True, truncate spans exceeding limits.

    Returns:
        Validated (and possibly truncated) span, or None.
    """
    limits = limits or DEFAULT_LIMITS

    if span is None:
        return None

    span = span.strip()
    if not span:
        return None

    if len(span) > limits.max_source_span_length:
        if truncate:
            return span[: limits.max_source_span_length] + "…[truncated]"
        raise ContentValidationError(
            f"Source span too long: {len(span):,} chars "
            f"(max: {limits.max_source_span_length:,})",
        )

    return span


def validate_extraction_fields(
    fields: list,
    limits: ContentLimits | None = None,
) -> list:
    """Validate extraction results don't exceed field limits.

    Args:
        fields: List of extracted fields.
        limits: Custom limits to apply.

    Returns:
        Truncated list of fields if needed.
    """
    limits = limits or DEFAULT_LIMITS

    if len(fields) > limits.max_extraction_fields:
        return fields[: limits.max_extraction_fields]

    return fields


def validate_extraction_errors(
    errors: list[str],
    limits: ContentLimits | None = None,
) -> list[str]:
    """Validate extraction errors don't exceed limits.

    Args:
        errors: List of validation errors.
        limits: Custom limits to apply.

    Returns:
        Truncated list of errors if needed.
    """
    limits = limits or DEFAULT_LIMITS

    if len(errors) > limits.max_validation_errors:
        return errors[: limits.max_validation_errors]

    return errors
