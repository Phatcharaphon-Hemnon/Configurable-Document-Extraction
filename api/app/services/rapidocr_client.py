"""Local OCR client based on RapidOCR (ONNX runtime).

All uploads (images + PDFs) are OCR'd on this host into plain text per page,
which then flows through the unchanged text-only pipeline
(Router → Extractor → Validator → Judge) using the single configured text
model. No vision model, API key, or network access required after the small
ONNX models (~15 MB) are downloaded on first use.

Why RapidOCR: the ONNX runtime runs on Python 3.14 and CPU with ~1 s/page,
no GPU framework needed.

Public interface::

    parse_file(file_bytes, filename) -> list[str]   # one text per page
    aparse_file(file_bytes, filename) -> list[str]  # async wrapper

Quality notes
-------------
* PDFs are rendered with PyMuPDF at 300 DPI (configurable) — then each page
  is OCR'd.
* Text boxes are sorted top-to-bottom, left-to-right and grouped into lines
  so reading order (and rough table row order) is preserved without
  inventing structure.
* No confidence filtering: every recognised token is kept and joined. Low
  quality tokens are left for the downstream Validator/Judge (source_span +
  confidence) instead of being silently dropped here.
* Script support: the bundled models cover Latin text and digits (invoices,
  receipts, POs). Non-Latin scripts such as Thai have limited support.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class RapidOCRError(Exception):
    """Raised when local OCR fails (missing dependency, corrupt file, ...)."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_pdf_bytes(data: bytes, filename: str | None = None) -> bool:
    if data[:5] == b"%PDF-":
        return True
    return bool(filename) and Path(filename).suffix.lower() == ".pdf"


class RapidOCRClient:
    """Thin wrapper around RapidOCR with PDF rendering and result caching.

    Parameters
    ----------
    dpi: PDF render resolution. 300 is the recommended quality class.
    enable_cache: cache OCR results by file sha256 in memory.
    """

    def __init__(self, dpi: int = 300, enable_cache: bool = True) -> None:
        self.dpi = max(150, min(int(dpi or 300), 600))
        self.enable_cache = bool(enable_cache)
        self._engine: Any | None = None
        self._engine_lock = threading.Lock()
        self._cache: dict[str, list[str]] = {}
        self._cache_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Engine lifecycle (lazy — first use downloads the ONNX models)
    # ------------------------------------------------------------------

    def _ensure_engine(self) -> Any:
        with self._engine_lock:
            if self._engine is not None:
                return self._engine
            try:
                from rapidocr_onnxruntime import RapidOCR  # type: ignore
            except ImportError as exc:
                raise RapidOCRError(
                    "rapidocr_onnxruntime is not installed. Install it with: "
                    "pip install rapidocr_onnxruntime pymupdf pillow numpy"
                ) from exc
            try:
                self._engine = RapidOCR()
            except Exception as exc:
                raise RapidOCRError(f"Failed to initialise RapidOCR: {exc}") from exc
            logger.info("RapidOCR engine initialised")
            return self._engine

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_file(self, file_input: bytes | str | Path, filename: str | None = None) -> list[str]:
        """OCR a file and return one text string per page."""
        if isinstance(file_input, (str, Path)):
            path = Path(file_input)
            data = path.read_bytes()
            filename = filename or path.name
        elif isinstance(file_input, (bytes, bytearray)):
            data = bytes(file_input)
        else:
            raise RapidOCRError(f"Unsupported input type: {type(file_input)}")

        if not data:
            raise RapidOCRError("Empty file content")

        cache_key = _sha256(data) + f":{self.dpi}" if self.enable_cache else ""
        if cache_key:
            with self._cache_lock:
                cached = self._cache.get(cache_key)
            if cached is not None:
                logger.info("OCR cache hit (%s, %d pages)", filename or "upload", len(cached))
                return list(cached)

        try:
            if _is_pdf_bytes(data, filename):
                pages = self._ocr_pdf_bytes(data)
            else:
                pages = [self._ocr_image_bytes(data)]
        except RapidOCRError:
            raise
        except Exception as exc:
            raise RapidOCRError(f"OCR failed for {filename or 'upload'}: {exc}") from exc

        if self.enable_cache and cache_key:
            with self._cache_lock:
                # Bound cache size (keep last 128 documents).
                if len(self._cache) >= 128:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[cache_key] = list(pages)
        return pages

    async def aparse_file(self, file_input: bytes | str | Path, filename: str | None = None) -> list[str]:
        """Async wrapper — runs blocking OCR in a worker thread."""
        return await asyncio.to_thread(self.parse_file, file_input, filename)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ocr_pdf_bytes(self, pdf_bytes: bytes) -> list[str]:
        try:
            import fitz  # PyMuPDF
        except ImportError as exc:
            raise RapidOCRError("PyMuPDF is required for PDFs: pip install pymupdf") from exc
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as exc:
            raise RapidOCRError(f"Cannot open PDF: {exc}") from exc
        pages: list[str] = []
        try:
            zoom = self.dpi / 72.0
            for page in doc:
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
                img_bytes = pix.tobytes("png")
                pages.append(self._ocr_image_bytes(img_bytes))
        finally:
            doc.close()
        if not pages:
            raise RapidOCRError("PDF has no pages")
        return pages

    def _ocr_image_bytes(self, image_bytes: bytes) -> str:
        engine = self._ensure_engine()
        image = self._bytes_to_array(image_bytes)
        try:
            result, _elapse = engine(image)
        except Exception as exc:
            raise RapidOCRError(f"RapidOCR engine call failed: {exc}") from exc
        lines = self._raw_to_lines(result)
        return "\n".join(lines).strip()

    @staticmethod
    def _bytes_to_array(image_bytes: bytes) -> Any:
        try:
            import numpy as np
            from PIL import Image
        except ImportError as exc:
            raise RapidOCRError("pillow and numpy are required: pip install pillow numpy") from exc
        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                img = img.convert("RGB")
                return np.array(img)
        except Exception as exc:
            raise RapidOCRError(f"Cannot decode image ({len(image_bytes)} bytes): {exc}") from exc

    @staticmethod
    def _raw_to_lines(raw: Any) -> list[str]:
        """Normalise RapidOCR output into ordered text lines.

        RapidOCR returns ``[[box, text, conf], ...]`` (or ``None`` when
        nothing is recognised). Boxes are sorted into reading order:
        top-to-bottom rows, left-to-right within each row.
        """
        texts_with_boxes: list[tuple[str, float, float]] = []  # (text, cx, cy)

        def add_boxed(text: object, box: Any) -> None:
            t = str(text or "").strip()
            if not t:
                return
            try:
                xs = [float(p[0]) for p in box]
                ys = [float(p[1]) for p in box]
                cx = sum(xs) / len(xs)
                cy = sum(ys) / len(ys)
            except Exception:
                cx, cy = 0.0, 0.0
            texts_with_boxes.append((t, cx, cy))

        def walk(node: Any) -> None:
            if node is None:
                return
            if isinstance(node, (list, tuple)):
                # Leaf: [box, text, conf] (conf optional).
                if (
                    len(node) in (2, 3)
                    and isinstance(node[0], (list, tuple))
                    and isinstance(node[1], str)
                ):
                    add_boxed(node[1], node[0])
                    return
                for item in node:
                    walk(item)
                return
            if isinstance(node, str) and node.strip():
                texts_with_boxes.append((node.strip(), 0.0, 0.0))

        walk(raw)

        if not texts_with_boxes:
            return []
        # Reading order: sort by vertical centre, group rows whose centres
        # are within a line tolerance, then sort each row left-to-right.
        texts_with_boxes.sort(key=lambda e: (e[2], e[1]))
        rows: list[list[tuple[str, float, float]]] = []
        for entry in texts_with_boxes:
            placed = False
            for row in rows:
                if abs(row[0][2] - entry[2]) <= 12.0:
                    row.append(entry)
                    placed = True
                    break
            if not placed:
                rows.append([entry])
        lines = [" ".join(t for t, _, _ in sorted(row, key=lambda e: e[1])) for row in rows]
        return [line for line in (s.strip() for s in lines) if line]
