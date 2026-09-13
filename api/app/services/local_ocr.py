"""Cancellable local Thai/English OCR with page geometry. See docs/multilingual_ocr.md
and docs/thai_catalog_hybrid_ocr.md (opt-in hybrid)."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import NamedTuple
from uuid import uuid4

from PIL import Image, ImageOps

from app.core.security import (
    COHERENCE_THRESHOLD,
    is_ocr_text_coherent,
    ocr_text_coherence,
)
from app.schemas.ocr import OCRBlock, OCRPage
from app.services.rapidocr_client import RapidOCRClient, RapidOCRError


def layout_text(blocks: list[OCRBlock]) -> str:
    """Group words by relative height, retaining horizontal gaps as table separators."""
    rows: list[list[OCRBlock]] = []
    for block in sorted(blocks, key=lambda b: (b.box[1] + b.box[3] / 2, b.box[0])):
        center = block.box[1] + block.box[3] / 2
        if rows and abs(center - (rows[-1][0].box[1] + rows[-1][0].box[3] / 2)) < max(5, block.box[3] * 0.6):
            rows[-1].append(block)
        else:
            rows.append([block])
    lines = []
    for row in rows:
        row.sort(key=lambda b: b.box[0])
        text = row[0].text
        for previous, block in zip(row, row[1:]):
            gap = block.box[0] - previous.box[0] - previous.box[2]
            separator = " | " if gap > max(previous.box[3], block.box[3]) * 1.5 else " "
            if (
                separator == " "
                and re.search(r"[\u0e00-\u0e7f]$", previous.text)
                and re.match(r"^[\u0e00-\u0e7f]", block.text)
            ):
                separator = ""
            text += separator + block.text
        lines.append(text)
    return "\n".join(lines)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared document loading (single implementation).
#
# RapidOCRClient.parse_file/aparse_file delegate page rendering to these
# helpers so PDF handling, image decoding, and upsampling exist exactly once.
# Recognition stays engine-specific (Tesseract / RapidOCR / hybrid below).
# ---------------------------------------------------------------------------

class LoadedPage(NamedTuple):
    """One rendered page: PIL RGB image + PDF context for preprocessing."""

    image: Image.Image
    is_pdf: bool
    has_native_text: bool


def is_pdf_document(data: bytes, filename: str | None = None) -> bool:
    """True for PDF magic bytes or a .pdf filename (mirrors upload sniffing)."""
    return data.startswith(b"%PDF-") or (filename or "").lower().endswith(".pdf")


def coerce_file_bytes(
    file_input: bytes | str | Path, filename: str | None = None
) -> tuple[bytes, str | None]:
    """Normalise parse_file input to (raw bytes, filename)."""
    if isinstance(file_input, (str, Path)):
        path = Path(file_input)
        return path.read_bytes(), filename or path.name
    if isinstance(file_input, (bytes, bytearray)):
        return bytes(file_input), filename
    raise RapidOCRError(f"Unsupported input type: {type(file_input)}")


def _maybe_upsample(img: Image.Image) -> Image.Image:
    # Small template screenshots have 5–8px text; upsample before OCR.
    if min(img.size) < 1200:
        factor = min(3.0, 1200 / min(img.size))
        img = img.resize(
            (round(img.width * factor), round(img.height * factor)), Image.Resampling.LANCZOS
        )
    return img


def load_page_images(
    data: bytes, filename: str | None = None, dpi: int = 300
) -> list[LoadedPage]:
    """Render every page of a PDF or image file to PIL RGB images.

    PDFs render via PyMuPDF at ``dpi`` (native-text presence reported per
    page for grid-line preprocessing); images decode via Pillow with EXIF
    orientation applied (TIFF keeps every frame, other formats read one
    page). Raises RapidOCRError for pageless documents; corrupt inputs raise
    the decoder's own error for the caller to wrap or report.
    """
    if is_pdf_document(data, filename):
        import pymupdf

        doc = pymupdf.open(stream=data, filetype="pdf")
        try:
            if not len(doc):
                raise RapidOCRError("Document has no pages")
            pages = []
            for index in range(len(doc)):
                pix = doc[index].get_pixmap(dpi=dpi, alpha=False)
                img = _maybe_upsample(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
                pages.append(LoadedPage(img, True, bool(doc[index].get_text().strip())))
            return pages
        finally:
            doc.close()
    doc = Image.open(io.BytesIO(data))
    try:
        count = getattr(doc, "n_frames", 1) if doc.format == "TIFF" else 1
        if not count:
            raise RapidOCRError("Document has no pages")
        pages = []
        for index in range(count):
            doc.seek(index)
            img = _maybe_upsample(ImageOps.exif_transpose(doc).convert("RGB"))
            pages.append(LoadedPage(img, False, False))
        return pages
    finally:
        doc.close()


class LocalOCRClient:
    def __init__(self, settings):
        self.settings = settings
        self.dpi = settings.ocr_dpi
        self.last_pages: list[OCRPage] = []
        self._cache: dict[str, list[OCRPage]] = {}
        self.cache_dir = Path(settings.ocr_cache_path)
        self._rapid = RapidOCRClient(dpi=self.dpi)
        self._trocr = None  # lazy TrOCR client for hybrid only
        # Fingerprint the actual model bytes once; changing models invalidates OCR cache.
        models = Path(settings.tessdata_dir) if settings.tessdata_dir else Path("/usr/share/tessdata")
        self.model_hashes = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for lang in settings.ocr_languages.split("+")
            if (p := models / f"{lang}.traineddata").is_file()
        }

    def _hybrid_fingerprint(self) -> dict:
        """Model revisions + recognition settings + preprocessing version."""
        try:
            rapid_revs = self._rapid.model_revisions()
        except Exception:
            rapid_revs = {}
        return {
            "rapid": rapid_revs,
            "trocr_model": getattr(self.settings, "hybrid_trocr_model", "microsoft/trocr-base-handwritten"),
            "trocr_revision": getattr(self.settings, "hybrid_trocr_revision", ""),
            "trocr_threshold": getattr(self.settings, "hybrid_trocr_conf_threshold", 0.80),
            "trocr_max_regions": getattr(self.settings, "hybrid_trocr_max_regions", 10),
            "preprocess": getattr(self.settings, "hybrid_preprocess_version", "hybrid-v1"),
            "rapid_pin": "3.9.2",
        }

    async def aparse_file(
        self, data: bytes, filename: str | None = None, use_cache: bool | None = None,
    ) -> list[str]:
        """OCR every page. `use_cache=False` bypasses memory + disk OCR caches
        (benchmark/debug); None follows settings.ocr_cache_enabled."""
        self.last_pages = []
        cache_on = self.settings.ocr_cache_enabled if use_cache is None else bool(use_cache)
        key = hashlib.sha256(data).hexdigest() + repr(
            (
                self.dpi,
                self.settings.ocr_engine,
                self.settings.ocr_languages,
                self.model_hashes,
                "layout-v5-digital-rules",
                "rapid-fallback-v1",
                "rapid-ppocrv5-th-en-v1",
                self._hybrid_fingerprint(),
                # OCR/recovery policy changes invalidate relevant caches.
                f"coherence-{COHERENCE_THRESHOLD:.2f}",
            )
        )
        key = hashlib.sha256(key.encode()).hexdigest()
        if cache_on and key not in self._cache:
            self._load_disk_cache(key)
        if cache_on and key in self._cache:
            self.last_pages = [
                p.model_copy(update={"cached": True, "seconds": 0, "render_seconds": 0}) for p in self._cache[key]
            ]
            return [p.text for p in self.last_pages]
        loaded = load_page_images(data, filename, self.dpi)
        for loaded_page in loaded:
            img = loaded_page.image
            is_pdf = loaded_page.is_pdf
            page = OCRPage()
            self.last_pages.append(page)
            started = time.perf_counter()
            try:
                preview = img.copy()
                preview.thumbnail((1500, 2000))
                buffer = io.BytesIO()
                preview.save(buffer, format="PNG")
                page.preview = buffer.getvalue()
                page.render_seconds = time.perf_counter() - started
                ocr_start = time.perf_counter()
                engine_name = (self.settings.ocr_engine or "tesseract").strip().lower()
                if engine_name == "rapidocr":
                    raw = io.BytesIO()
                    img.save(raw, format="PNG")
                    text, blocks = await asyncio.wait_for(
                        asyncio.to_thread(self._rapid.ocr_blocks, raw.getvalue()),
                        self.settings.ocr_timeout_seconds,
                    )
                    page.text = text
                    page.blocks = blocks
                    page.engine = "rapidocr"
                    page.engines_used = ["rapidocr-th"]
                    try:
                        page.model_revisions = self._rapid.model_revisions()
                    except Exception:
                        page.model_revisions = {}
                    if text.strip() and not is_ocr_text_coherent(text):
                        page.review_reasons.append(
                            "OCR text incoherent (coherence "
                            f"{ocr_text_coherence(text):.2f} < {COHERENCE_THRESHOLD:.2f}): likely "
                            "handwriting or a degraded scan; no recovery applies to "
                            "single-engine output"
                        )
                elif engine_name == "hybrid":
                    raw = io.BytesIO()
                    img.save(raw, format="PNG")
                    blocks, engines_used, review_reasons, revisions = await asyncio.wait_for(
                        asyncio.to_thread(self._hybrid_page, raw.getvalue()),
                        self.settings.ocr_timeout_seconds,
                    )
                    page.blocks = blocks
                    page.text = layout_text(blocks)
                    page.engine = "hybrid"
                    page.engines_used = engines_used
                    page.model_revisions = revisions
                    page.review_reasons = review_reasons
                    if page.text.strip() and not is_ocr_text_coherent(page.text):
                        page.review_reasons.append(
                            "OCR text incoherent (coherence "
                            f"{ocr_text_coherence(page.text):.2f} < {COHERENCE_THRESHOLD:.2f}) "
                            "after hybrid recovery: handwriting could not be read reliably"
                        )
                else:
                    page.blocks = await self._tesseract(
                        img, remove_rules=is_pdf and loaded_page.has_native_text
                    )
                    page.text = layout_text(page.blocks)
                    page.engine = "tesseract"
                    page.engines_used = ["tesseract"]
                    # Fallback: Tesseract emits script-salad on some scans
                    # (e.g. Thai traineddata on Latin handwriting). Upgraded
                    # RapidOCR (PP-OCRv5 TH: Thai+English) is tried ONLY when
                    # the default text scores incoherent — never as a global
                    # switch. Bounded: OCR takes seconds.
                    if page.text.strip() and not is_ocr_text_coherent(page.text):
                        orig_coh = ocr_text_coherence(page.text)
                        orig_blocks = list(page.blocks)
                        logger.info(
                            "OCR primary tesseract incoherent (coherence %.2f < %.2f, "
                            "%d blocks, %d chars, langs=%s): attempting RapidOCR-TH fallback",
                            orig_coh, COHERENCE_THRESHOLD, len(orig_blocks),
                            len(page.text), self.settings.ocr_languages,
                        )
                        fallback, fallback_blocks, detail = await self._rapid_fallback(img)
                        if fallback is not None:
                            fb_coh = ocr_text_coherence(fallback)
                            logger.info(
                                "OCR fallback: rapidocr-th replaced incoherent "
                                "tesseract text (%d chars, coh %.2f) with %d chars "
                                "(coh %.2f, %d blocks with geometry preserved)",
                                len(page.text), orig_coh, len(fallback), fb_coh,
                                len(fallback_blocks),
                            )
                            # Preserve geometry: fallback blocks carry their own
                            # boxes/engine/provenance (never []); the original
                            # Tesseract reading stays distinguishable via the
                            # review trail + engines_used (not silently dropped).
                            page.text = fallback
                            page.blocks = list(fallback_blocks)
                            page.engine = "tesseract+rapidocr-fallback"
                            page.engines_used = ["tesseract", "rapidocr-th"]
                            page.review_reasons.append(
                                f"OCR recovery: tesseract incoherent ({orig_coh:.2f}) "
                                f"replaced by rapidocr-th ({fb_coh:.2f}); original "
                                f"{len(orig_blocks)} Tesseract regions preserved in engines_used"
                            )
                            try:
                                page.model_revisions = {
                                    **page.model_revisions,
                                    **self._rapid.model_revisions(),
                                }
                            except Exception:
                                pass
                        else:
                            page.review_reasons.append(
                                "OCR text incoherent (coherence "
                                f"{orig_coh:.2f} < {COHERENCE_THRESHOLD:.2f}): "
                                "likely handwriting or a degraded scan; RapidOCR recovery "
                                f"{detail}"
                            )
                page.seconds = time.perf_counter() - ocr_start
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                page.error = f"OCR failed: {exc}"
                page.seconds = time.perf_counter() - started
        if cache_on and all(not p.error for p in self.last_pages):
            if len(self._cache) >= 32:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = [p.model_copy(deep=True) for p in self.last_pages]
            self._save_disk_cache(key)
        return [p.text for p in self.last_pages]

    def _load_disk_cache(self, key: str) -> None:
        import base64

        from pydantic import ValidationError

        path = self.cache_dir / f"{key}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            pages = [OCRPage.model_validate(item["page"]) for item in raw]
            for page, item in zip(pages, raw):
                page.preview = base64.b64decode(item["preview"], validate=True) if item["preview"] else None
            if pages and not any(p.error for p in pages):
                if len(self._cache) >= 32:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[key] = pages
                path.touch()
        except (OSError, ValueError, KeyError, TypeError, ValidationError):
            # Corrupt/stale cache entries are misses, never extraction failures.
            return

    def _save_disk_cache(self, key: str) -> None:
        import base64

        temp = self.cache_dir / f".{key}.{uuid4().hex}.tmp"
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            entries = [
                {
                    "page": p.model_dump(mode="json"),
                    "preview": base64.b64encode(p.preview).decode("ascii") if p.preview else None,
                }
                for p in self.last_pages
            ]
            temp.write_text(json.dumps(entries, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            temp.replace(self.cache_dir / f"{key}.json")
            entries_on_disk = sorted(self.cache_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            for stale in entries_on_disk[self.settings.ocr_cache_max_files :]:
                stale.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("OCR cache write skipped: %s", exc)
        finally:
            temp.unlink(missing_ok=True)

    async def _rapid_fallback(self, img: Image.Image) -> tuple[str | None, list, str]:
        """One-shot RapidOCR retry for incoherent Tesseract output.

        Returns (replacement text or None, blocks with geometry, recovery
        detail). The replacement is used only when it is coherent itself;
        otherwise None and the caller keeps the original text/blocks while the
        pipeline's coherence gate reports honestly with the detail string.
        Never raises. Records engine/model/lang, elapsed, coherence, and
        readiness for every attempt (attempted/unavailable/failed/no-coherent).
        """
        started = time.perf_counter()
        try:
            raw = io.BytesIO()
            img.save(raw, format="PNG")
            payload = raw.getvalue()
            # Use ocr_blocks (not _ocr_image_bytes) so geometry is preserved:
            # replacement text always ships with its boxes/engine/confidence.
            text, blocks = await asyncio.wait_for(
                asyncio.to_thread(self._rapid.ocr_blocks, payload),
                self.settings.ocr_timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 — fallback must never break OCR
            elapsed = time.perf_counter() - started
            logger.warning(
                "OCR fallback unavailable/failed: engine=rapidocr-th lang=th-en "
                "elapsed=%.1fs error=%s",
                elapsed, exc,
            )
            return None, [], f"unavailable ({exc})"
        elapsed = time.perf_counter() - started
        coh = ocr_text_coherence(text) if text and text.strip() else 0.0
        # Validate geometry against the source page (non-empty boxes within
        # the rendered image bounds; zero-area boxes are dropped downstream).
        try:
            w, h = img.size
            valid = sum(
                1 for b in (blocks or [])
                if b.box[2] > 0 and b.box[3] > 0
                and b.box[0] >= -w and b.box[1] >= -h
                and b.box[0] <= 2 * w and b.box[1] <= 2 * h
            )
        except Exception:
            valid = 0
        if text and is_ocr_text_coherent(text):
            logger.info(
                "OCR fallback succeeded: engine=rapidocr-th rec=PP-OCRv5-mobile-TH "
                "elapsed=%.1fs chars=%d blocks=%d valid_geometry=%d coherence=%.2f",
                elapsed, len(text), len(blocks or []), valid, coh,
            )
            return text, list(blocks or []), "succeeded"
        logger.info(
            "OCR fallback produced no coherent text: engine=rapidocr-th "
            "elapsed=%.1fs chars=%d coherence=%.2f status=recognition-completed-unreadable",
            elapsed, len(text or ""), coh,
        )
        return None, [], "produced no coherent text"

    def _get_trocr(self):
        """Lazy TrOCR client for hybrid (None until first hybrid page)."""
        if self._trocr is not None:
            return self._trocr
        from app.services.trocr_client import TrOCRClient

        self._trocr = TrOCRClient(
            model_name=getattr(self.settings, "hybrid_trocr_model", "microsoft/trocr-base-handwritten"),
            revision=getattr(self.settings, "hybrid_trocr_revision", None) or None,
        )
        return self._trocr

    def _hybrid_page(self, image_bytes: bytes):
        """Blocking hybrid OCR for one page (runs in a worker thread).

        Detect once → TH → EN retry → selective TrOCR. Bounded by the
        caller's ``asyncio.wait_for`` (OCR timeout); also checks a monotonic
        deadline before each TrOCR crop. Raises on primary failure (caller
        keeps the existing page-error path); EN/TrOCR failures preserve
        RapidOCR text + review reasons (see hybrid_ocr.py).
        """
        import time as _time

        from app.services.hybrid_ocr import hybrid_ocr_page

        deadline = _time.monotonic() + float(self.settings.ocr_timeout_seconds)
        try:
            trocr = self._get_trocr()
        except Exception as exc:  # noqa: BLE001 — TrOCR ctor never fails hard
            logger.warning("Hybrid TrOCR init failed: %s", exc)
            trocr = None
        return hybrid_ocr_page(
            image_bytes,
            settings=self.settings,
            rapid_client=self._rapid,
            trocr_client=trocr,
            deadline=deadline,
        )

    async def _tesseract(self, img: Image.Image, *, remove_rules: bool = False) -> list[OCRBlock]:
        if remove_rules:
            # Born-digital PDF grids confuse segmentation. Do not apply this
            # morphological operation to photographed pages or their text strokes.
            import cv2
            import numpy as np

            gray = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY)
            ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
            horizontal = cv2.morphologyEx(
                ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(60, img.width // 12), 1))
            )
            vertical = cv2.morphologyEx(
                ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(60, img.height // 12)))
            )
            gray[(horizontal | vertical) > 0] = 255
            img = Image.fromarray(gray)
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        args = [
            self.settings.tesseract_cmd,
            "stdin",
            "stdout",
            "-l",
            self.settings.ocr_languages,
            "--oem",
            "1",
            "--psm",
            "6",
        ]
        if self.settings.tessdata_dir:
            args += ["--tessdata-dir", self.settings.tessdata_dir]
        args += ["tsv"]
        env = dict(os.environ)
        env["OMP_THREAD_LIMIT"] = "2"
        local_lib = Path(self.settings.tesseract_cmd).parent.parent / "lib"
        if local_lib.is_dir() and Path(self.settings.tesseract_cmd).is_absolute():
            env["LD_LIBRARY_PATH"] = str(local_lib) + (
                ":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else ""
            )
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(buffer.getvalue()), self.settings.ocr_timeout_seconds)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise RapidOCRError(err.decode(errors="replace")[:500])
        blocks = []
        for row in csv.DictReader(io.StringIO(out.decode("utf-8")), delimiter="\t", quoting=csv.QUOTE_NONE):
            if not row.get("text", "").strip() or row["level"] != "5":
                continue
            blocks.append(
                OCRBlock(
                    text=row["text"],
                    confidence=max(0, float(row["conf"]) / 100),
                    box=tuple(float(row[k]) for k in ("left", "top", "width", "height")),
                )
            )
        return blocks
