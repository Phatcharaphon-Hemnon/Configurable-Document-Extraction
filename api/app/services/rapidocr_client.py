"""Local OCR client based on the maintained ``rapidocr`` package (ONNX CPU).

All uploads (images + PDFs) are OCR'd on this host into plain text per page,
which then flows through the unchanged text-only pipeline
(Router → Extractor → Validator → Judge) using the single configured text
model. No vision model, API key, or network access required after the ONNX
models are downloaded on first use.

Model configuration (explicit, never package defaults)
------------------------------------------------------
* Detection: PP-OCRv5 mobile, ONNXRuntime CPU (``LangDet.CH`` — PaddleOCR
  detection is language-agnostic; the multilingual Det entry is ``CH``).
* Recognition TH: PP-OCRv5 mobile Thai+English (``LangRec.TH``).
* Recognition EN: PP-OCRv5 mobile English (``LangRec.EN``).
* Orientation classifier: PP-OCRv5 mobile (``CH``) — 0/180 correction.

The TH recogniser covers Thai+English; EN is a specialised retry for
Latin-only uncertain regions (see ``hybrid_ocr.py``). Detection runs once;
crops reuse the same boxes.

Public interface::

    parse_file(file_bytes, filename) -> list[str]   # one text per page
    aparse_file(file_bytes, filename) -> list[str]  # async wrapper
    ocr_detailed(image_bytes) -> DetailedOCR        # boxes+texts+scores
    recognize_crops(crops, lang="en")               # rec-only, no re-detect

Quality notes
-------------
* PDFs are rendered with PyMuPDF at 300 DPI (configurable) — then each page
  is OCR'd.
* Text boxes are sorted top-to-bottom, left-to-right and grouped into lines
  so reading order (and rough table row order) is preserved without
  inventing structure. Wide column gaps become ``" | "`` separators.
* No confidence filtering in the plain-text path: every recognised token is
  kept and joined. Low quality tokens are left for the downstream
  Validator/Judge (source_span + confidence) instead of being silently
  dropped here.
* Script support: TH model covers Thai+English printed text; EN covers
  Latin+digits. Thai handwriting remains unsupported.

Provenance: ``rapidocr==3.9.2`` (tested), ONNXRuntime CPU. Models hosted on
ModelScope, auto-downloaded on first use then cached. See
``docs/thai_catalog_hybrid_ocr.md`` for provisioning/offline use.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

# Tested dependency pin (see docs/thai_catalog_hybrid_ocr.md + requirements).
RAPIDOCR_PINNED_VERSION = "3.9.2"
RAPIDOCR_DET_SPEC = "PP-OCRv5/mobile/det/ch/onnxruntime"
RAPIDOCR_REC_TH_SPEC = "PP-OCRv5/mobile/rec/th/onnxruntime"
RAPIDOCR_REC_EN_SPEC = "PP-OCRv5/mobile/rec/en/onnxruntime"
RAPIDOCR_CLS_SPEC = "PP-OCRv5/mobile/cls/ch/onnxruntime"
RAPIDOCR_OCR_VERSION = "PP-OCRv5"


class RapidOCRError(Exception):
    """Raised when local OCR fails (missing dependency, corrupt file, ...)."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _page_png(image: Any) -> bytes:
    """Encode one shared-loader PIL page for the recognition engine."""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@dataclass
class DetailedOCR:
    """Single-image detection + TH recognition result for hybrid reuse."""

    image: Any  # np.ndarray HxWx3 RGB
    boxes: Any  # np.ndarray (N,4,2) float32 — original detection polygons
    txts_th: list[str]
    scores_th: list[float]


class RapidOCRClient:
    """Image detection + recognition with maintained ``rapidocr``.

    Document loading and PDF rendering live in
    :mod:`app.services.local_ocr` (``load_page_images``); the
    file-processing methods below delegate to that shared implementation
    and keep only a small in-memory memo. The canonical pipeline cache
    (fingerprinted memory + disk) belongs to ``LocalOCRClient``.

    Parameters
    ----------
    dpi: PDF render resolution. 300 is the recommended quality class.
    enable_cache: memoize OCR results by file sha256 in memory.
    """

    def __init__(self, dpi: int = 300, enable_cache: bool = True) -> None:
        self.dpi = max(150, min(int(dpi or 300), 600))
        self.enable_cache = bool(enable_cache)
        self._th_engine: Any | None = None
        self._en_engine: Any | None = None
        self._engine_lock = threading.Lock()
        self._infer_lock = threading.Lock()
        self._cache: dict[str, list[str]] = {}
        self._cache_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Engine lifecycle (lazy — first use downloads the ONNX models)
    # ------------------------------------------------------------------

    def _rapidocr_symbols(self) -> dict[str, Any]:
        try:
            from rapidocr import (  # type: ignore
                EngineType,
                LangDet,
                LangRec,
                ModelType,
                OCRVersion,
                RapidOCR,
            )
        except ImportError as exc:
            raise RapidOCRError(
                "rapidocr is not installed. Install it with: "
                f"pip install rapidocr=={RAPIDOCR_PINNED_VERSION} onnxruntime pymupdf pillow numpy "
                "(see docs/thai_catalog_hybrid_ocr.md)"
            ) from exc
        return {
            "RapidOCR": RapidOCR,
            "EngineType": EngineType,
            "LangDet": LangDet,
            "LangRec": LangRec,
            "ModelType": ModelType,
            "OCRVersion": OCRVersion,
        }

    def _build_engine(self, rec_lang: Literal["th", "en"]) -> Any:
        sym = self._rapidocr_symbols()
        RapidOCR = sym["RapidOCR"]
        EngineType = sym["EngineType"]
        LangDet = sym["LangDet"]
        LangRec = sym["LangRec"]
        ModelType = sym["ModelType"]
        OCRVersion = sym["OCRVersion"]
        rec = LangRec.TH if rec_lang == "th" else LangRec.EN
        params = {
            "Det.engine_type": EngineType.ONNXRUNTIME,
            "Det.lang_type": LangDet.CH,
            "Det.model_type": ModelType.MOBILE,
            "Det.ocr_version": OCRVersion.PPOCRV5,
            "Cls.engine_type": EngineType.ONNXRUNTIME,
            "Cls.lang_type": LangDet.CH,
            "Cls.model_type": ModelType.MOBILE,
            "Cls.ocr_version": OCRVersion.PPOCRV5,
            "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Rec.lang_type": rec,
            "Rec.model_type": ModelType.MOBILE,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
        }
        try:
            engine = RapidOCR(params=params)
        except Exception as exc:
            raise RapidOCRError(f"Failed to initialise RapidOCR ({rec_lang}): {exc}") from exc
        logger.info("RapidOCR engine initialised (rec=%s, %s)", rec_lang, RAPIDOCR_DET_SPEC)
        return engine

    def _ensure_engine(self, rec_lang: Literal["th", "en"] = "th") -> Any:
        with self._engine_lock:
            if rec_lang == "th":
                if self._th_engine is None:
                    self._th_engine = self._build_engine("th")
                return self._th_engine
            if self._en_engine is None:
                self._en_engine = self._build_engine("en")
            return self._en_engine

    # Back-compat: legacy callers use `_ensure_engine()` with no args.
    # Default to the TH engine (Thai+English coverage).

    def model_revisions(self) -> dict[str, str]:
        """Fingerprintable model identifiers (no network, static pins)."""
        try:
            import rapidocr  # type: ignore

            pkg = getattr(rapidocr, "__version__", "unknown")
        except Exception:
            pkg = f"missing (pinned {RAPIDOCR_PINNED_VERSION})"
        return {
            "rapidocr_pkg": str(pkg),
            "rapidocr_pinned": RAPIDOCR_PINNED_VERSION,
            "det": RAPIDOCR_DET_SPEC,
            "rec_th": RAPIDOCR_REC_TH_SPEC,
            "rec_en": RAPIDOCR_REC_EN_SPEC,
            "cls": RAPIDOCR_CLS_SPEC,
            "ocr_version": RAPIDOCR_OCR_VERSION,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_file(self, file_input: bytes | str | Path, filename: str | None = None) -> list[str]:
        """OCR a file and return one text string per page.

        Page rendering delegates to ``local_ocr.load_page_images`` (single
        implementation for PDF/image decoding); recognition stays here.
        """
        from app.services.local_ocr import coerce_file_bytes, load_page_images

        data, filename = coerce_file_bytes(file_input, filename)

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
            pages = [self._ocr_image_bytes(_page_png(p.image)) for p in load_page_images(data, filename, self.dpi)]
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
    # Detailed API for hybrid (detect once, recognise twice)
    # ------------------------------------------------------------------

    def ocr_detailed(self, image_bytes: bytes) -> DetailedOCR:
        """Detect once + TH recognition. Returns boxes/polys for crop reuse."""
        engine = self._ensure_engine("th")
        image = self._bytes_to_array(image_bytes)
        with self._infer_lock:
            try:
                result = engine(image)
            except Exception as exc:
                raise RapidOCRError(f"RapidOCR-TH engine call failed: {exc}") from exc
        boxes, txts, scores = self._unpack_rapid_output(result, image)
        return DetailedOCR(image=image, boxes=boxes, txts_th=txts, scores_th=scores)

    def recognize_crops(
        self, crops: list[Any], lang: Literal["th", "en"] = "en"
    ) -> tuple[list[str], list[float]]:
        """Recognition-only retry on already-cropped line images.

        Uses ``use_det=False, use_cls=False, use_rec=True`` so regions are
        never re-detected — the caller's boxes are authoritative.
        """
        if not crops:
            return [], []
        engine = self._ensure_engine(lang)
        txts: list[str] = []
        scores: list[float] = []
        with self._infer_lock:
            for crop in crops:
                try:
                    result = engine(crop, use_det=False, use_cls=False, use_rec=True)
                except Exception as exc:
                    raise RapidOCRError(f"RapidOCR-{lang} crop call failed: {exc}") from exc
                t, s = self._unpack_rec_only(result)
                txts.append(t)
                scores.append(s)
        return txts, scores

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ocr_image_bytes(self, image_bytes: bytes) -> str:
        text, _ = self.ocr_blocks(image_bytes)
        return text

    def ocr_blocks(self, image_bytes: bytes) -> tuple[str, list]:
        """OCR one image to (laid-out text, recognition blocks).

        Blocks carry engine provenance, confidences, and geometry for the
        coherence gate and review propagation; text uses the shared
        ``local_ocr.layout_text`` (single table-gap implementation).
        """
        from app.schemas.ocr import OCRBlock
        from app.services.local_ocr import layout_text

        detailed = self.ocr_detailed(image_bytes)
        blocks = [
            OCRBlock(text=text, confidence=conf, box=box, engine="rapidocr-th")
            for text, conf, box in self.detailed_to_blocks(
                detailed.boxes, detailed.txts_th, detailed.scores_th
            )
        ]
        return layout_text(blocks), blocks

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

    def _unpack_rapid_output(self, result: Any, image: Any) -> tuple[Any, list[str], list[float]]:
        """Normalise new ``RapidOCROutput`` (or legacy tuple) to (boxes, txts, scores)."""
        if result is None:
            import numpy as np

            return np.zeros((0, 4, 2), dtype=float), [], []
        # New API: RapidOCROutput with .boxes/.txts/.scores
        if hasattr(result, "txts") and hasattr(result, "boxes"):
            try:
                boxes = result.boxes
            except Exception:
                import numpy as np

                boxes = np.zeros((0, 4, 2), dtype=float)
            txts = [str(t or "") for t in (result.txts or [])]
            try:
                scores = [float(s) for s in (result.scores or [])]
            except Exception:
                scores = [0.0] * len(txts)
            if boxes is None:
                import numpy as np

                boxes = np.zeros((0, 4, 2), dtype=float)
            return boxes, txts, scores
        # Legacy tuple: (result_list, elapse) where result_list is [[box, text, conf]]
        raw = result[0] if isinstance(result, (list, tuple)) and len(result) == 2 else result
        items = self._legacy_items(raw)
        import numpy as np

        if not items:
            return np.zeros((0, 4, 2), dtype=float), [], []
        boxes = np.array([b for b, _, _ in items], dtype=float)
        return boxes, [t for _, t, _ in items], [s for _, _, s in items]

    @staticmethod
    def _legacy_items(raw: Any) -> list[tuple[Any, str, float]]:
        out: list[tuple[Any, str, float]] = []

        def walk(node: Any) -> None:
            if node is None:
                return
            if isinstance(node, (list, tuple)) and len(node) in (2, 3) and isinstance(node[0], (list, tuple)) and isinstance(node[1], str):
                conf = 0.0
                try:
                    conf = float(node[2]) if len(node) == 3 else 0.0
                except Exception:
                    conf = 0.0
                out.append((node[0], node[1], conf))
                return
            if isinstance(node, (list, tuple)):
                for item in node:
                    walk(item)

        walk(raw)
        return out

    @staticmethod
    def _unpack_rec_only(result: Any) -> tuple[str, float]:
        if result is None:
            return "", 0.0
        if hasattr(result, "txts"):
            txts = list(result.txts or [])
            scores = list(result.scores or [])
            if not txts:
                return "", 0.0
            try:
                conf = float(scores[0]) if scores else 0.0
            except Exception:
                conf = 0.0
            return str(txts[0] or ""), conf
        # Legacy: ([[box, text, conf]], elapse)
        raw = result[0] if isinstance(result, (list, tuple)) and len(result) == 2 else result
        items = RapidOCRClient._legacy_items(raw)
        if not items:
            if isinstance(raw, str) and raw.strip():
                return raw.strip(), 0.0
            return "", 0.0
        return str(items[0][1]), float(items[0][2])

    # -- block helpers (layout itself lives in local_ocr.layout_text) --

    @staticmethod
    def detailed_to_blocks(boxes: Any, txts: list[str], scores: list[float]) -> list[tuple[str, float, tuple[float, float, float, float]]]:
        """Polys → axis-aligned (text, conf, (x,y,w,h)) for layout grouping."""
        blocks: list[tuple[str, float, tuple[float, float, float, float]]] = []
        try:
            n = len(txts)
        except Exception:
            return blocks
        for i in range(n):
            text = str(txts[i] or "").strip()
            if not text:
                continue
            try:
                conf = float(scores[i]) if i < len(scores) else 0.0
            except Exception:
                conf = 0.0
            try:
                poly = boxes[i]
                xs = [float(p[0]) for p in poly]
                ys = [float(p[1]) for p in poly]
                x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
                blocks.append((text, conf, (x0, y0, x1 - x0, y1 - y0)))
            except Exception:
                blocks.append((text, conf, (0.0, 0.0, 0.0, 0.0)))
        return blocks

    @staticmethod
    def _raw_to_lines(raw: Any) -> list[str]:
        """Legacy helper kept for tests: normalise old ``[[box,text,conf]]`` to lines."""
        items = RapidOCRClient._legacy_items(raw)
        if not items:
            if isinstance(raw, str) and raw.strip():
                return [raw.strip()]
            # New-style object passed by mistake — degrade to txts join.
            if hasattr(raw, "txts"):
                try:
                    return [str(t).strip() for t in (raw.txts or []) if str(t).strip()]
                except Exception:
                    return []
            return []
        texts_with_boxes: list[tuple[str, float, float]] = []
        for box, text, _ in items:
            t = str(text or "").strip()
            if not t:
                continue
            try:
                xs = [float(p[0]) for p in box]
                ys = [float(p[1]) for p in box]
                cx = sum(xs) / len(xs)
                cy = sum(ys) / len(ys)
            except Exception:
                cx, cy = 0.0, 0.0
            texts_with_boxes.append((t, cx, cy))
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

    # -- cropping (detect-once, correct orientation, retain boxes) --

    @staticmethod
    def crop_region(image: Any, poly: Any) -> Any | None:
        """Perspective-crop one detection polygon with 180° correction.

        Returns an RGB array suitable for rec-only retry (RapidOCR rec or
        TrOCR line recogniser), or None when the polygon is degenerate.
        """
        try:
            import copy

            import cv2
            import numpy as np
        except ImportError as exc:
            raise RapidOCRError(f"opencv/numpy required for hybrid crops: {exc}") from exc
        try:
            points = np.array(poly, dtype=np.float32)
            if points.shape != (4, 2):
                return None
            width = int(max(np.linalg.norm(points[0] - points[1]), np.linalg.norm(points[2] - points[3])))
            height = int(max(np.linalg.norm(points[0] - points[3]), np.linalg.norm(points[1] - points[2])))
            if width < 2 or height < 2 or width * height < 16:
                return None
            # Cap giant crops (full-page fallback) — TrOCR is a LINE recogniser.
            if width > 3000 or height > 800:
                return None
            dst = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
            tmp = copy.deepcopy(points)
            matrix = cv2.getPerspectiveTransform(tmp, dst)
            crop = cv2.warpPerspective(
                image, matrix, (width, height),
                borderMode=cv2.BORDER_REPLICATE, flags=cv2.INTER_CUBIC,
            )
            if crop is None or crop.size == 0:
                return None
            h, w = crop.shape[0:2]
            if h * 1.0 / max(1, w) >= 1.5:
                crop = np.rot90(crop)
            # RapidOCR/TrOCR expect RGB uint8.
            if crop.ndim == 2:
                crop = cv2.cvtColor(crop, cv2.COLOR_GRAY2RGB)
            elif crop.shape[2] == 4:
                crop = cv2.cvtColor(crop, cv2.COLOR_RGBA2RGB)
            elif crop.shape[2] == 3:
                # Input image is RGB; cv2 warp preserves channel order.
                pass
            return crop
        except Exception:
            return None
