"""Real format decoding, page isolation and shared workflow regressions."""

import io
from unittest.mock import AsyncMock

import pymupdf
import pytest
from PIL import Image

from app.core.config import Settings
from app.schemas.ocr import OCRBlock
from app.services.local_ocr import LocalOCRClient, layout_text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "suffix,format",
    [
        ("jpg", "JPEG"),
        ("jpeg", "JPEG"),
        ("png", "PNG"),
        ("webp", "WEBP"),
        ("bmp", "BMP"),
        ("gif", "GIF"),
        ("tif", "TIFF"),
        ("tiff", "TIFF"),
    ],
)
async def test_supported_image_decoders_preserve_text_and_preview(suffix, format):
    settings = Settings()
    settings.ocr_cache_enabled = False
    client = LocalOCRClient(settings)
    client._tesseract = AsyncMock(
        return_value=[OCRBlock(text="ใบกำกับภาษี Invoice", confidence=0.9, box=(1, 1, 100, 20))]
    )
    data = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(data, format=format)
    assert await client.aparse_file(data.getvalue(), f"doc.{suffix}") == ["ใบกำกับภาษี Invoice"]
    assert client.last_pages[0].preview.startswith(b"\x89PNG")


@pytest.mark.asyncio
@pytest.mark.parametrize("format", ["pdf", "tiff"])
async def test_middle_page_ocr_failure_does_not_drop_later_pages(format):
    data = io.BytesIO()
    if format == "pdf":
        with pymupdf.open() as doc:
            for _ in range(3):
                doc.new_page(width=40, height=40)
            raw = doc.tobytes()
    else:
        frames = [Image.new("RGB", (40, 40), color) for color in ["white", "gray", "black"]]
        frames[0].save(data, format="TIFF", save_all=True, append_images=frames[1:])
        raw = data.getvalue()
    settings = Settings()
    settings.ocr_cache_enabled = False
    client = LocalOCRClient(settings)
    client._tesseract = AsyncMock(
        side_effect=[
            [OCRBlock(text="ใบกำกับภาษี", confidence=0.9, box=(1, 1, 100, 20))],
            TimeoutError("OCR timeout"),
            [OCRBlock(text="Purchase order", confidence=0.9, box=(1, 1, 100, 20))],
        ]
    )
    texts = await client.aparse_file(raw, f"mixed.{format}")
    assert texts == ["ใบกำกับภาษี", "", "Purchase order"]
    assert len(client.last_pages) == 3
    assert client.last_pages[1].error and client.last_pages[2].preview


def test_thai_tokens_join_and_table_gaps_survive():
    blocks = [
        OCRBlock(text=t, confidence=0.9, box=b)
        for t, b in [
            ("จำนวน", (0, 0, 20, 10)),
            ("สินค้า", (22, 0, 20, 10)),
            ("ราคา", (100, 0, 20, 10)),
        ]
    ]
    assert layout_text(blocks) == "จำนวนสินค้า | ราคา"


@pytest.mark.asyncio
async def test_temporal_document_uses_shared_page_pipeline(monkeypatch):
    from app.temporal import workflows

    execute = AsyncMock(side_effect=[["invoice", "purchase"], {"doc_type": "invoice"}, {"doc_type": "purchase_order"}])
    monkeypatch.setattr(workflows.workflow, "execute_activity", execute)
    result = await workflows.ExtractDocumentWorkflow().run("mixed.pdf", b"pdf")
    assert [d["doc_type"] for d in result["documents"]] == ["invoice", "purchase_order"]
    calls = execute.call_args_list
    assert len(calls[0].args) == 1
    assert all(c.args[0] is workflows.process_page_activity for c in calls[1:])
    assert all(c.kwargs["retry_policy"].maximum_attempts == 1 for c in calls)
