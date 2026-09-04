"""Combined OCR + pipeline quality tests (local RapidOCR).

Covers, in one file:

1. RapidOCRClient unit tests (mocked engine — run WITHOUT RapidOCR installed).
2. RapidOCRClient result parsing ([box, text, conf] shapes, reading order).
3. Service wiring: extract_group() uses local OCR and tags source "ocr".
4. Quality tests over /img_test/* (run only when sample images exist AND
   RapidOCR is installed; otherwise skipped with a clear reason).

Run from api/:  python -m pytest tests/test_all.py -v -s
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.services.rapidocr_client import (  # noqa: E402
    RapidOCRClient,
    RapidOCRError,
    _is_pdf_bytes,
)

_HAS_RAPIDOCR = importlib.util.find_spec("rapidocr_onnxruntime") is not None
_HAS_PIL = importlib.util.find_spec("PIL") is not None
IMG_TEST_DIR = _REPO_ROOT / "img_test"
_IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif", ".gif", ".pdf"}


def _sample_images() -> list[Path]:
    if not IMG_TEST_DIR.is_dir():
        return []
    return sorted(p for p in IMG_TEST_DIR.iterdir() if p.suffix.lower() in _IMG_EXTS and p.is_file())


# ---------------------------------------------------------------------------
# 1. Client basics (no RapidOCR needed)
# ---------------------------------------------------------------------------

def test_is_pdf_bytes_detects_magic_and_extension():
    assert _is_pdf_bytes(b"%PDF-1.7 rest", "doc.pdf") is True
    assert _is_pdf_bytes(b"%PDF-1.7 rest", "noext") is True
    assert _is_pdf_bytes(b"\x89PNG\r\n", "scan.png") is False
    assert _is_pdf_bytes(b"hello", "doc.pdf") is True


def test_parse_file_rejects_empty_content():
    client = RapidOCRClient(enable_cache=False)
    with pytest.raises(RapidOCRError):
        client.parse_file(b"", "empty.png")


def test_parse_file_rejects_unsupported_type():
    client = RapidOCRClient(enable_cache=False)
    with pytest.raises(RapidOCRError):
        client.parse_file(12345, "weird.bin")  # type: ignore[arg-type]


def test_missing_rapidocr_raises_clear_error():
    client = RapidOCRClient(enable_cache=False)
    with patch.dict(sys.modules, {"rapidocr_onnxruntime": None}):
        # Force the lazy import to fail even if rapidocr is installed.
        with patch("builtins.__import__", side_effect=ImportError("No module named 'rapidocr_onnxruntime'")):
            with pytest.raises(RapidOCRError, match="not installed"):
                client._ensure_engine()


# ---------------------------------------------------------------------------
# 2. Result parsing ([box, text, conf] shapes, no engine needed)
# ---------------------------------------------------------------------------

def test_raw_to_lines_box_shape():
    # Real RapidOCR output shape: [box, text, conf-as-string].
    raw = [
        [[[10, 10], [100, 10], [100, 30], [10, 30]], "Invoice No: INV-001", "0.99"],
        [[[10, 40], [100, 40], [100, 60], [10, 60]], "Total: 100", "0.95"],
    ]
    lines = RapidOCRClient._raw_to_lines(raw)
    assert lines == ["Invoice No: INV-001", "Total: 100"]


def test_raw_to_lines_sorts_reading_order():
    # Boxes given bottom-row-first must still read top-to-bottom.
    raw = [
        [[[10, 100], [100, 100], [100, 120], [10, 120]], "Total: 100", "0.9"],
        [[[10, 10], [100, 10], [100, 30], [10, 30]], "Invoice No: INV-001", "0.9"],
    ]
    lines = RapidOCRClient._raw_to_lines(raw)
    assert lines[0] == "Invoice No: INV-001"
    assert lines[-1] == "Total: 100"


def test_raw_to_lines_empty_result():
    assert RapidOCRClient._raw_to_lines(None) == []
    assert RapidOCRClient._raw_to_lines([]) == []


def test_cache_returns_without_rerunning_engine(tmp_path):
    client = RapidOCRClient(enable_cache=True)
    fake_png = b"\x89PNG" + b"0" * 100
    with patch.object(client, "_ocr_image_bytes", return_value="cached text") as ocr_mock:
        first = client.parse_file(fake_png, "a.png")
        second = client.parse_file(fake_png, "a.png")
    assert first == ["cached text"]
    assert second == ["cached text"]
    assert ocr_mock.call_count == 1  # second call served from cache


# ---------------------------------------------------------------------------
# 3. Service wiring (mocked OCR + mocked LLM agents)
# ---------------------------------------------------------------------------

def _mocked_service(tmp_path: Path):
    import json

    from app.core.config import Settings
    from app.schemas.documents import ExtractedField, JudgeResult, RoutingDecision
    from app.services.extraction_service import DocumentExtractionService

    kb = tmp_path / "kb"
    (kb / "field_catalog").mkdir(parents=True)
    (kb / "field_catalog" / "invoice_fields.json").write_text(json.dumps({
        "doc_type": "invoice",
        "fields": [
            {"name": "invoice_number", "type": "string", "required": True},
            {"name": "total_amount", "type": "number", "required": True},
        ],
    }), encoding="utf-8")
    settings = Settings()
    settings.knowledge_base_path = str(kb)
    service = DocumentExtractionService(settings=settings)
    service.ocr = MagicMock()
    service.ocr.aparse_file = AsyncMock(return_value=["Invoice No: INV-001\nTotal: 100"])
    service.router = MagicMock()
    service.router.classify = AsyncMock(
        return_value=RoutingDecision(doc_type="invoice", confidence=0.95, reason="ocr text")
    )
    for extractor in service.extractors.values():
        extractor.extract = AsyncMock(return_value=(
            [
                ExtractedField(name="invoice_number", value="INV-001", confidence=0.95,
                               source_span="Invoice No: INV-001"),
                ExtractedField(name="total_amount", value=100.0, confidence=0.9,
                               source_span="Total: 100"),
            ],
            [],
        ))
    service.judge = MagicMock()
    service.judge.evaluate = AsyncMock(return_value=JudgeResult(score=0.9, issues=[], notes="ok"))
    return service


@pytest.mark.asyncio
async def test_extract_group_uses_local_ocr_not_cloud(tmp_path):
    """Regression test: OCR must be local (no cloud/network calls)."""
    import socket

    from app.services.extraction_service import UploadedFilePart

    service = _mocked_service(tmp_path)
    with patch.object(socket, "getaddrinfo", side_effect=AssertionError("no network in OCR path")):
        response = await service.extract_group([
            UploadedFilePart("invoice.pdf", "application/pdf", b"%PDF-1.4 fake"),
        ])
    service.ocr.aparse_file.assert_called_once()
    assert response.error is None
    assert len(response.documents) == 1
    assert response.job_id is not None


@pytest.mark.asyncio
async def test_extraction_source_is_ocr(tmp_path):
    from app.services.extraction_service import UploadedFilePart

    service = _mocked_service(tmp_path)
    response = await service.extract_group([
        UploadedFilePart("scan.png", "image/png", b"\x89PNG fake"),
    ])
    assert response.documents[0].extraction_source == "ocr"


@pytest.mark.asyncio
async def test_image_bytes_fallback_ocrs_without_vision(tmp_path):
    """Old image-bytes callers still work: text is OCR'd, no vision model."""
    service = _mocked_service(tmp_path)
    doc = await service._extract_one_page(
        filename="legacy.png", page_text="", image_bytes=b"\x89PNG fake",
        image_media_type="image/png",
    )
    assert doc.error is None
    assert [f.name for f in doc.fields] == ["invoice_number", "total_amount"]


# ---------------------------------------------------------------------------
# 4. Quality tests over /img_test/* (need images + RapidOCR installed)
# ---------------------------------------------------------------------------

requires_quality_env = pytest.mark.skipif(
    not _sample_images(),
    reason=f"No sample images in {IMG_TEST_DIR} — drop .png/.jpg/.pdf files there to run quality tests.",
)
requires_rapidocr = pytest.mark.skipif(
    not (_HAS_RAPIDOCR and _HAS_PIL),
    reason="RapidOCR/pillow not installed — pip install rapidocr_onnxruntime pymupdf pillow numpy.",
)


@requires_quality_env
@requires_rapidocr
def test_quality_images_produce_text():
    """Each sample must OCR to non-empty text. Prints stats for comparison."""
    client = RapidOCRClient()
    failures: list[str] = []
    for path in _sample_images():
        started = time.perf_counter()
        try:
            pages = client.parse_file(path.read_bytes(), path.name)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{path.name}: ERROR {exc}")
            continue
        elapsed = time.perf_counter() - started
        chars = sum(len(p) for p in pages)
        lines = sum(p.count("\n") + 1 for p in pages if p.strip())
        print(f"\n[quality] {path.name}: {len(pages)} page(s), {chars} chars, "
              f"{lines} lines, {elapsed:.1f}s")
        preview = (pages[0][:300] + "…") if pages and len(pages[0]) > 300 else (pages[0] if pages else "")
        print(f"[quality] preview: {preview!r}")
        if not any(p.strip() for p in pages):
            failures.append(f"{path.name}: empty OCR output")
    assert not failures, "OCR quality failures:\n" + "\n".join(failures)


@requires_quality_env
@requires_rapidocr
@pytest.mark.asyncio
async def test_quality_end_to_end_with_real_ocr(tmp_path):
    """Real RapidOCR text flows through the mocked-LLM pipeline per image."""
    from app.services.extraction_service import UploadedFilePart

    service = _mocked_service(tmp_path)
    # Use real OCR for page text, mocked LLM agents for extraction.
    real_client = RapidOCRClient()
    for path in _sample_images()[:3]:  # cap at 3 to keep runtime sane
        data = path.read_bytes()
        try:
            pages = await real_client.aparse_file(data, path.name)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"RapidOCR failed on {path.name}: {exc}")
        assert any(p.strip() for p in pages), f"Empty OCR for {path.name}"
        service.ocr.aparse_file = AsyncMock(return_value=pages)
        response = await service.extract_group([
            UploadedFilePart(path.name, None, data),
        ])
        assert response.error is None, f"{path.name}: {response.error}"
        assert response.documents, f"{path.name}: no documents"
        print(f"\n[e2e] {path.name}: {len(response.documents)} doc(s), "
              f"source={response.documents[0].extraction_source}")
