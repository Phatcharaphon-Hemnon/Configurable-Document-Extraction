from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.documents import DocumentLanguage


class SuggestedFieldSchema(BaseModel):
    name: str
    description: str | None = None
    likely_required: bool = False


class RoutingResponseSchema(BaseModel):
    """Open-schema routing response. doc_type is a free-form string proposed
    by the model (not restricted to a fixed enum) — e.g. 'invoice', 'receipt',
    'medical_form', 'id_card', or anything else the model recognizes.
    """

    doc_type: str = Field(description="Free-form document type label, e.g. 'invoice', 'receipt', 'id_card'.")
    language: DocumentLanguage
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)
    suggested_fields: list[SuggestedFieldSchema] = Field(
        default_factory=list,
        description="Fields this document type is expected to contain, based on what the model can see. "
        "Mark likely_required=true only for fields essential to identify/use this specific document.",
    )


class ExtractedFieldEntry(BaseModel):
    name: str
    value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    source_span: str | None = None
    likely_required: bool = False


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