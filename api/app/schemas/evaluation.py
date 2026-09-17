"""Gold-set evaluation contracts. See docs/evaluation.md.

Additive schema-extension policy: new fields are optional with defaults that
reproduce legacy meaning exactly (old manifests validate unchanged and score
identically). Schema ``version`` bumps only on breaking or
validation-tightening changes; adding documents/pages never bumps it.
Serialization is therefore NOT byte-identical across versions — equivalence
is semantic (same scores for legacy content), not textual.
"""

from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.schemas.documents import DocType


class GoldTable(BaseModel):
    name: str
    columns: list[str]
    rows: list[list[str | float | None]]


class AnnotationRef(BaseModel):
    """Pointer to an original annotation artifact (machine-readable store).

    The bytes at ``path`` (relative to the gold directory) are the authority;
    ``sha256`` pins them. Derived data must bind to this hash, never to a
    staging or temporary path.
    """

    path: str
    sha256: str
    kind: str = ""  # e.g. "funsd-original", "funsd-derived", "sroie-box"


class GoldEntity(BaseModel):
    id: int | str
    text: str = ""
    box: list[float] = Field(default_factory=list)
    label: str = ""
    words: list[dict[str, Any]] = Field(default_factory=list)


class GoldLink(BaseModel):
    source: int | str
    target: int | str
    repeated: bool = False  # repeated declarations preserved, not deduped away


class DerivedAnnotations(BaseModel):
    """Validated typed view over an original FUNSD-style annotation.

    QA pairs derive ONLY from explicit links; unlinked entities and ambiguous
    links are recorded separately. The raw original is never normalized here.
    """

    source_sha256: str = ""
    entities: list[GoldEntity] = Field(default_factory=list)
    links: list[GoldLink] = Field(default_factory=list)
    unlinked_ids: list[int | str] = Field(default_factory=list)
    ambiguous: list[dict[str, Any]] = Field(default_factory=list)


class DatasetProvenance(BaseModel):
    """Identity of an externally sourced gold document. All optional so
    legacy manifests validate unchanged."""

    source: str = ""  # e.g. "sroie" | "docile" | "thai_receipt" | "local"
    split: str = ""
    source_id: str = ""
    license: str = ""
    provenance_url: str = ""
    retrieved_at: str = ""


class GoldPage(BaseModel):
    page_number: int = Field(ge=1)
    # None only with document_kind="form" (evaluation-only extension; the
    # production DocType Literal is unchanged and never gains a form member).
    doc_type: DocType | None = None
    # Evaluation-level kind. None means "same as doc_type" (legacy behavior).
    document_kind: str | None = None
    # Production type override. None means "same as doc_type"; must stay null
    # for kind "form" (FUNSD has no production type claim).
    production_doc_type: DocType | None = None
    language: str
    fields: dict[str, Any]
    tables: list[GoldTable] = Field(default_factory=list)
    excluded_fields: list[str] = Field(default_factory=list)
    notes: str = ""
    # Scoped-scoring controls (None/False = legacy behavior: everything scored).
    # annotation_scope lists the in-scope normalized field/cell names; anything
    # else predicted is ignored (not a false positive) and recorded separately.
    annotation_scope: list[str] | None = None
    # Names recorded as annotated-but-unsupported for extraction scoring
    # (e.g. a receipt date with no production-comparable field). They are
    # reported separately and never count as false negatives.
    unsupported_fields: list[str] = Field(default_factory=list)
    # routing_excluded marks extraction-only pages (e.g. receipt-only sources):
    # router accuracy skips them instead of scoring them.
    routing_excluded: bool = False
    # Machine-readable annotation stores (paths resolve against the gold
    # directory; free-text notes are human-readable only, never the store).
    annotation_refs: list[AnnotationRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_kind_combination(self) -> "GoldPage":
        kind = self.document_kind
        if kind is None:
            if self.doc_type is None:
                raise ValueError("doc_type is required unless document_kind is set")
            if self.production_doc_type is not None and self.production_doc_type != self.doc_type:
                raise ValueError("production_doc_type conflicts with doc_type")
            return self
        if kind == "form":
            if self.doc_type is not None or self.production_doc_type is not None:
                raise ValueError("kind 'form' must not carry a production type")
            return self
        raise ValueError(f"unknown document_kind: {kind!r}")

    def effective_kind(self) -> str:
        """Kind used by the evaluator: explicit kind, else the doc_type."""
        if self.document_kind is not None:
            return self.document_kind
        return str(self.doc_type)


class GoldFile(BaseModel):
    filename: str
    sha256: str
    pages: list[GoldPage]
    dataset: DatasetProvenance | None = None


class GoldManifest(BaseModel):
    version: int
    annotation_method: str
    release_subset: list[str]
    files: list[GoldFile]
