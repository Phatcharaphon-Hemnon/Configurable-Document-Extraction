from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from Backend.app.schemas.documents import DocumentLanguage, DocumentType


class RoutingResponseSchema(BaseModel):
    doc_type: DocumentType
    language: DocumentLanguage
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)


class ExtractedFieldEntry(BaseModel):
    name: str
    value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    source_span: str | None = None


class ExtractionResponseSchema(BaseModel):
    fields: list[ExtractedFieldEntry] = Field(default_factory=list)


class JudgeIssueSchema(BaseModel):
    field: str
    message: str
    severity: str = "warning"


class JudgeResponseSchema(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    issues: list[JudgeIssueSchema] = Field(default_factory=list)
    notes: str
