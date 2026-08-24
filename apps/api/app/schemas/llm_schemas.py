"""Schemas the LLM must return (used with the structured-output client)."""

from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, Field


class RoutingResponseSchema(BaseModel):
    """Router output — doc_type restricted to the 3 fixed types."""

    doc_type: Literal["invoice", "purchase_order", "delivery_note"]
    language: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: Optional[str] = None


class ExtractedFieldEntry(BaseModel):
    name: str
    value: Optional[Union[str, float, int, list, dict]] = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source_span: Optional[str] = None


class ExtractionResponseSchema(BaseModel):
    fields: list[ExtractedFieldEntry] = Field(default_factory=list)


class JudgeIssueEntry(BaseModel):
    field: str
    message: str
    severity: Literal["info", "warning", "error"] = "warning"


class JudgeResponseSchema(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    issues: list[JudgeIssueEntry] = Field(default_factory=list)
    notes: str = ""
