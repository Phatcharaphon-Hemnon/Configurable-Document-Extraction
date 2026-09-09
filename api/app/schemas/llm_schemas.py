"""Schemas the LLM must return (used with the structured-output client)."""

from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.documents import ExtractedTable


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
    model_config = ConfigDict(extra="forbid")

    fields: list[ExtractedFieldEntry]
    tables: list[ExtractedTable] = Field(default_factory=list)


class JudgeIssueEntry(BaseModel):
    field: str
    message: str
    severity: Literal["info", "warning", "error"] = "warning"


class JudgeResponseSchema(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    issues: list[JudgeIssueEntry] = Field(default_factory=list)
    notes: str = ""
