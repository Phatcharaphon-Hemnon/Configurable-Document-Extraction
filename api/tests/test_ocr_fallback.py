"""RapidOCR fallback for incoherent Tesseract output (ICR salvage).

Tesseract emits script-salad on some scans (Thai traineddata on Latin
handwriting). RapidOCR covers Latin+digits only, so it is tried ONLY when
the default text is incoherent — never as a global switch (it cannot read
Thai). All paths are bounded (OCR takes seconds) and never raise.
"""

import io
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.core.config import Settings  # noqa: E402
from app.schemas.ocr import OCRBlock  # noqa: E402
from app.services.local_ocr import LocalOCRClient  # noqa: E402

# ICR-class soup: long enough to trip the gate, mostly OCR noise.
SOUP = ("— TAXINVOICE : 86 BELASTINGFAKTUUR Bin 2% ๕๕ _ , โญ ภ ล ท ให ศร เบ 1 ๕ "
        ". อ ไฮ ก ค ‘ ed r SB ั 77 /7 : ( NA B.T.W.Reg Nr ร่ - ี 3-- ี 33@ "
        "เ [60 | | SIGE OVC Sard 1 โอ ห ท ร Subtotaal Terme V.A.T. inclusive a "
        "ea pea จ อ ไก Delete as applicable Skrap waar nie van toepassing nie "
        "TOTAL ๒ 3 —_— TOTAAL | | | ๑ ๒ ๓")
COHERENT = ("TAX INVOICE BELASTINGFAKTUUR Date Datum 12/7/2014 "
            "160 100 45 Sub Total Subtotaal V.A.T. Inclusive TOTAL 305 TOTAAL")


def _client(soup_text: str = SOUP) -> LocalOCRClient:
    settings = Settings()
    settings.ocr_cache_enabled = False
    client = LocalOCRClient(settings)
    words = soup_text.split()
    # Wide pitch mimics real OCR geometry: column gaps become " | "
    # separators in layout_text (which is what the gate scores).
    boxes = [OCRBlock(text=w, confidence=0.5, box=(i * 30, 1, 9, 10)) for i, w in enumerate(words)]
    client._tesseract = AsyncMock(return_value=boxes)
    return client


def _image() -> bytes:
    data = io.BytesIO()
    Image.new("RGB", (1300, 200), "white").save(data, format="PNG")
    return data.getvalue()


def _coherent_blocks(text: str = COHERENT):
    from app.services.local_ocr import layout_text

    words = text.split()
    blocks = [OCRBlock(text=w, confidence=0.9, box=(i * 30, 1, 9, 10), engine="rapidocr-th")
              for i, w in enumerate(words)]
    return layout_text(blocks), blocks


@pytest.mark.asyncio
async def test_incoherent_tesseract_falls_back_to_rapidocr():
    client = _client()
    fb_text, fb_blocks = _coherent_blocks()
    client._rapid.ocr_blocks = MagicMock(return_value=(fb_text, fb_blocks))
    texts = await client.aparse_file(_image(), "icr.png")
    # layout_text preserves table gaps as " | " separators — check tokens, not raw spacing.
    assert texts and "TAX" in texts[0] and "INVOICE" in texts[0]
    # Geometry must be preserved: replacement ships with its own boxes/engine,
    # never an empty block list (original Tesseract reading stays in provenance).
    assert client.last_pages[0].blocks, "fallback must preserve geometry"
    assert all(b.engine == "rapidocr-th" for b in client.last_pages[0].blocks)
    assert client.last_pages[0].engine == "tesseract+rapidocr-fallback"


@pytest.mark.asyncio
async def test_incoherent_rapid_keeps_original_text():
    client = _client()
    # Letter-salad fallback (no usable words): incoherent under the
    # coherence rule, so the original Tesseract text is kept. Pure
    # separator/digit strings carry no script signal and are not used here.
    salad = "โญ ภ ล ท ให ศร เบ อ ไฮ ก ค " * 8
    salad_blocks = [OCRBlock(text=w, confidence=0.4, box=(i * 30, 1, 9, 10), engine="rapidocr-th")
                    for i, w in enumerate(salad.split())]
    client._rapid.ocr_blocks = MagicMock(return_value=(salad, salad_blocks))
    texts = await client.aparse_file(_image(), "icr.png")
    assert texts and "TAXINVOICE" in texts[0]


@pytest.mark.asyncio
async def test_coherent_tesseract_never_calls_rapidocr():
    client = _client("Purchase Orders 10256 2016-07-15 Paula Parente Products "
                     "Product Quantity Unit Price Perth Pasties Original Frankfurter")
    client._rapid.ocr_blocks = MagicMock(side_effect=AssertionError("must not be called"))
    texts = await client.aparse_file(_image(), "po.pdf")
    assert "Paula" in texts[0] and "Parente" in texts[0]


@pytest.mark.asyncio
async def test_rapid_failure_keeps_original_text():
    client = _client()
    client._rapid.ocr_blocks = MagicMock(side_effect=RuntimeError("onnx missing"))
    texts = await client.aparse_file(_image(), "icr.png")
    assert texts and "TAXINVOICE" in texts[0]
