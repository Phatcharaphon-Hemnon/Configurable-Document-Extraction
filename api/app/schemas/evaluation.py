"""Gold-set evaluation contracts. See docs/evaluation.md."""

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.documents import DocType


class GoldTable(BaseModel):
    name: str
    columns: list[str]
    rows: list[list[str | float | None]]


class GoldPage(BaseModel):
    page_number: int = Field(ge=1)
    doc_type: DocType
    language: str
    fields: dict[str, Any]
    tables: list[GoldTable] = Field(default_factory=list)
    excluded_fields: list[str] = Field(default_factory=list)
    notes: str = ""


class GoldFile(BaseModel):
    filename: str
    sha256: str
    pages: list[GoldPage]


class GoldManifest(BaseModel):
    version: int
    annotation_method: str
    release_subset: list[str]
    files: list[GoldFile]
