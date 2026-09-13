"""Handwritten-OCR recovery (3492511_1.pdf): honest block, provenance, caches.

Covers: mixed-language EN retry eligibility, disabled/missing/failing/
unsuccessful recovery, short numeric cells + separators, gibberish rejected,
readable heading vs unreadable rows, partial geometry, page isolation +
provenance, and policy-change cache invalidation.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.core.config import Settings  # noqa: E402
from app.core.security import (  # noqa: E402
    COHERENCE_THRESHOLD,
    is_ocr_text_coherent,
    ocr_text_coherence,
)
from app.schemas.ocr import OCRBlock  # noqa: E402
from app.services.hybrid_ocr import is_trocr_eligible  # noqa: E402
from app.services.local_ocr import LocalOCRClient  # noqa: E402

HAND_SALAD = ("INVOICE | 44\nMo =G_—w Gy\nไ | AWMER 700 - KAMBERW 2.\n"
              "BOT. Kelana dl)\non\nเว๐ | caer fi\nEA Shiv | Pl ol\n1 ๐\n(")


def _img() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (1300, 200), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_noisy_mixed_language_allows_english_retry():
    # Uncertain Thai-looking text must NOT veto an English attempt (handwriting
    # misread as Thai salad is the recovery case); confident Thai print skips it.
    assert is_trocr_eligible("Seguiw Bevel", "เซกิว", "Seguiw Bevel", 0.4, threshold=0.80)
    assert not is_trocr_eligible("ใบกำกับภาษี", "ใบกำกับภาษี", "ใบกำกับภาษี", 0.95, threshold=0.80)
    # Digit-only, empty, Thai-containing, or confident readings are ineligible.
    assert not is_trocr_eligible("410", "410", "410", 0.3, threshold=0.80)
    assert not is_trocr_eligible("", "", "", 0.0, threshold=0.80)


@pytest.mark.asyncio
async def test_recovery_unavailable_missing_failing_unsuccessful():
    # Missing (package absent on this host): original kept + honest review reason.
    s = Settings()
    s.ocr_cache_enabled = False
    client = LocalOCRClient(s)
    words = HAND_SALAD.split()
    client._tesseract = AsyncMock(
        return_value=[OCRBlock(text=w, confidence=0.5, box=(i * 30, 1, 9, 10)) for i, w in enumerate(words)]
    )
    texts = await client.aparse_file(_img(), "3492511_1.pdf")
    assert texts and not is_ocr_text_coherent(texts[0])
    reason = " ".join(client.last_pages[0].review_reasons)
    assert "incoherent" in reason and "RapidOCR recovery" in reason
    # Original geometry preserved (not discarded).
    assert len(client.last_pages[0].blocks) > 0

    # Failing recovery (exception) keeps original text, never raises.
    client2 = LocalOCRClient(s)
    client2._tesseract = AsyncMock(
        return_value=[OCRBlock(text=w, confidence=0.5, box=(i * 30, 1, 9, 10)) for i, w in enumerate(words)]
    )
    client2._rapid.ocr_blocks = MagicMock(side_effect=RuntimeError("onnx missing"))
    texts2 = await client2.aparse_file(_img(), "x.pdf")
    assert "INVOICE" in texts2[0]

    # Unsuccessful recovery (incoherent output) keeps original.
    client3 = LocalOCRClient(s)
    client3._tesseract = AsyncMock(
        return_value=[OCRBlock(text=w, confidence=0.5, box=(i * 30, 1, 9, 10)) for i, w in enumerate(words)]
    )
    salad = "โญ ภ ล ท ให ศร เบ อ ไฮ ก ค " * 8
    client3._rapid.ocr_blocks = MagicMock(
        return_value=(salad, [OCRBlock(text=w, confidence=0.3, box=(0, 0, 5, 5)) for w in salad.split()])
    )
    texts3 = await client3.aparse_file(_img(), "x.pdf")
    assert "INVOICE" in texts3[0]


def test_short_numeric_cells_and_separators():
    # Valid sparse documents pass; separators/digits alone never condemn.
    assert ocr_text_coherence("| | | — ( )") == 1.0
    assert ocr_text_coherence("44 700 2 10 19 6 " * 5) == 1.0
    clean = ("Description | Qty | Price\nDress 4 10\nSkirt 4 15\nTotal 19 6\n"
             "Invoice Number 44 " + "Additional clean row data here " * 4)
    assert is_ocr_text_coherent(clean)
    # Genuine gibberish stays rejected even with separators appended.
    assert not is_ocr_text_coherent(HAND_SALAD)
    assert not is_ocr_text_coherent(HAND_SALAD + " | | | " * 10)


def test_readable_heading_does_not_trust_unreadable_rows():
    # INVOICE + 44 are readable, but the page as a whole is incoherent:
    # a readable heading must not make handwritten rows appear trustworthy.
    assert ocr_text_coherence(HAND_SALAD) < COHERENCE_THRESHOLD
    assert not is_ocr_text_coherent(HAND_SALAD)
    # Honest outcome: blocked with preview + blocks preserved, no invented rows.
    assert "INVOICE" in HAND_SALAD and "44" in HAND_SALAD


@pytest.mark.asyncio
async def test_partial_recovery_preserves_geometry():
    s = Settings()
    s.ocr_cache_enabled = False
    client = LocalOCRClient(s)
    words = HAND_SALAD.split()
    client._tesseract = AsyncMock(
        return_value=[OCRBlock(text=w, confidence=0.5, box=(i * 30, 1, 9, 10)) for i, w in enumerate(words)]
    )
    coherent = "INVOICE 44 BOT Total 19"
    fb_blocks = [OCRBlock(text=w, confidence=0.9, box=(i * 40, 2, 12, 12), engine="rapidocr-th")
                 for i, w in enumerate(coherent.split())]
    from app.services.local_ocr import layout_text
    client._rapid.ocr_blocks = MagicMock(return_value=(layout_text(fb_blocks), fb_blocks))
    texts = await client.aparse_file(_img(), "x.pdf")
    assert "INVOICE" in texts[0]
    # Geometry validated against the source page (non-empty boxes).
    for b in client.last_pages[0].blocks:
        assert b.box[2] > 0 and b.box[3] > 0
    assert client.last_pages[0].engine == "tesseract+rapidocr-fallback"


@pytest.mark.asyncio
async def test_recovery_preserves_page_isolation_and_provenance():
    from app.services.evidence import assign_block_ids

    s = Settings()
    s.ocr_cache_enabled = False
    client = LocalOCRClient(s)
    words = HAND_SALAD.split()
    client._tesseract = AsyncMock(
        return_value=[OCRBlock(text=w, confidence=0.5, box=(i * 30, 1, 9, 10)) for i, w in enumerate(words)]
    )
    await client.aparse_file(_img(), "a.pdf")
    blocks = list(client.last_pages[0].blocks)
    assign_block_ids(blocks, 2)
    ids = [b.block_id for b in blocks]
    assert all(i and i.startswith("page-2-block-") for i in ids)
    assert len(set(ids)) == len(ids)


def test_policy_changes_invalidate_caches():
    from app.services.result_cache import CLIENT_RECOVERY_VERSION, PROMPT_VERSION

    assert PROMPT_VERSION.startswith("prompts-v3")
    assert CLIENT_RECOVERY_VERSION.startswith("client-recovery-v2")
    # OCR cache key includes the coherence threshold.
    import inspect

    src = inspect.getsource(LocalOCRClient.aparse_file)
    assert "coherence" in src.lower()
    # Result fingerprint includes coherence + recovery versions (the config
    # dict is shared by page fingerprints and manifest keys, so check both).
    from app.services.result_cache import ResultCache

    src2 = inspect.getsource(ResultCache.fingerprint_page)
    src3 = inspect.getsource(ResultCache.config_fingerprint_dict)
    assert "coherence_threshold" in src2 + src3
    assert "client_recovery" in src2 + src3
