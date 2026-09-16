"""Local OCR page and geometry contracts; see docs/multilingual_ocr.md."""

from pydantic import BaseModel, Field


class AlternativeReading(BaseModel):
    """One non-selected OCR reading retained for review.

    `engine` records provenance (e.g. "rapidocr-th", "rapidocr-en", "trocr").
    `confidence` is the recogniser's own score when available — TrOCR
    generation scores are NOT extraction confidence and must stay None.
    """

    text: str
    engine: str = ""
    confidence: float | None = None


class OCRBlock(BaseModel):
    text: str
    confidence: float
    box: tuple[float, float, float, float]
    # Engine provenance for this block's selected text
    # (e.g. "tesseract", "rapidocr-th", "rapidocr-en", "trocr").
    engine: str = "tesseract"
    # Non-selected readings kept for review (conflicting RapidOCR/TrOCR).
    alternatives: list[AlternativeReading] = Field(default_factory=list)
    # Why this region needs review (e.g. low confidence, conflict).
    review_reason: str | None = None
    # Stable per-page identifier (`page-{n}-block-{i}`), assigned by the OCR
    # layer. Evidence references resolve against these IDs; model-generated
    # IDs/coordinates are never trusted. None for legacy cached pages.
    block_id: str | None = None


class OCRPage(BaseModel):
    text: str = ""
    blocks: list[OCRBlock] = Field(default_factory=list)
    error: str | None = None
    seconds: float = 0
    render_seconds: float = 0
    cached: bool = False
    preview: bytes | None = Field(default=None, exclude=True)
    # Hybrid/provenance metadata — all backward-compatible defaults.
    # `engine`: selected pipeline ("tesseract" | "rapidocr" | "hybrid").
    engine: str = "tesseract"
    # Every engine that contributed to this page (e.g. ["rapidocr-th", "trocr"]).
    engines_used: list[str] = Field(default_factory=list)
    # Model revisions + recognition settings for cache fingerprints/debugging.
    model_revisions: dict[str, str] = Field(default_factory=dict)
    # Page-level reasons to force needs_review (propagated to validation_errors).
    review_reasons: list[str] = Field(default_factory=list)
