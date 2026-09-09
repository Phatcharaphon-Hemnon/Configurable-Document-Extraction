"""Local OCR page and geometry contracts; see docs/multilingual_ocr.md."""

from pydantic import BaseModel, Field


class OCRBlock(BaseModel):
    text: str
    confidence: float
    box: tuple[float, float, float, float]


class OCRPage(BaseModel):
    text: str = ""
    blocks: list[OCRBlock] = Field(default_factory=list)
    error: str | None = None
    seconds: float = 0
    render_seconds: float = 0
    cached: bool = False
    preview: bytes | None = Field(default=None, exclude=True)
