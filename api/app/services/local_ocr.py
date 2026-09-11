"""Cancellable local Thai/English OCR with page geometry. See docs/multilingual_ocr.md."""

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
from uuid import uuid4

from PIL import Image, ImageOps

from app.core.security import is_ocr_text_coherent
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


class LocalOCRClient:
    def __init__(self, settings):
        self.settings = settings
        self.dpi = settings.ocr_dpi
        self.last_pages: list[OCRPage] = []
        self._cache: dict[str, list[OCRPage]] = {}
        self.cache_dir = Path(settings.ocr_cache_path)
        self._rapid = RapidOCRClient(dpi=self.dpi)
        # Fingerprint the actual model bytes once; changing models invalidates OCR cache.
        models = Path(settings.tessdata_dir) if settings.tessdata_dir else Path("/usr/share/tessdata")
        self.model_hashes = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for lang in settings.ocr_languages.split("+")
            if (p := models / f"{lang}.traineddata").is_file()
        }

    async def aparse_file(self, data: bytes, filename: str | None = None) -> list[str]:
        self.last_pages = []
        key = hashlib.sha256(data).hexdigest() + repr(
            (
                self.dpi,
                self.settings.ocr_engine,
                self.settings.ocr_languages,
                self.model_hashes,
                "layout-v5-digital-rules",
                "rapid-fallback-v1",
            )
        )
        key = hashlib.sha256(key.encode()).hexdigest()
        if self.settings.ocr_cache_enabled and key not in self._cache:
            self._load_disk_cache(key)
        if self.settings.ocr_cache_enabled and key in self._cache:
            self.last_pages = [
                p.model_copy(update={"cached": True, "seconds": 0, "render_seconds": 0}) for p in self._cache[key]
            ]
            return [p.text for p in self.last_pages]
        import pymupdf

        is_pdf = data.startswith(b"%PDF-") or (filename or "").lower().endswith(".pdf")
        doc = pymupdf.open(stream=data, filetype="pdf") if is_pdf else Image.open(io.BytesIO(data))
        try:
            count = len(doc) if is_pdf else getattr(doc, "n_frames", 1) if doc.format == "TIFF" else 1
            if not count:
                raise RapidOCRError("Document has no pages")
            for index in range(count):
                page = OCRPage()
                self.last_pages.append(page)
                started = time.perf_counter()
                try:
                    if is_pdf:
                        pix = doc[index].get_pixmap(dpi=self.dpi, alpha=False)
                        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    else:
                        doc.seek(index)
                        img = ImageOps.exif_transpose(doc).convert("RGB")
                    # Small template screenshots have 5–8px text; upsample before OCR.
                    if min(img.size) < 1200:
                        factor = min(3.0, 1200 / min(img.size))
                        img = img.resize(
                            (round(img.width * factor), round(img.height * factor)), Image.Resampling.LANCZOS
                        )
                    preview = img.copy()
                    preview.thumbnail((1500, 2000))
                    buffer = io.BytesIO()
                    preview.save(buffer, format="PNG")
                    page.preview = buffer.getvalue()
                    page.render_seconds = time.perf_counter() - started
                    ocr_start = time.perf_counter()
                    if self.settings.ocr_engine == "rapidocr":
                        raw = io.BytesIO()
                        img.save(raw, format="PNG")
                        page.text = await asyncio.wait_for(
                            asyncio.to_thread(self._rapid._ocr_image_bytes, raw.getvalue()),
                            self.settings.ocr_timeout_seconds,
                        )
                    else:
                        page.blocks = await self._tesseract(
                            img, remove_rules=is_pdf and bool(doc[index].get_text().strip())
                        )
                        page.text = layout_text(page.blocks)
                        # Fallback: Tesseract emits script-salad on some scans
                        # (e.g. Thai traineddata on Latin handwriting). RapidOCR
                        # covers Latin+digits only, so it is tried ONLY when the
                        # default text is incoherent — never as a global switch
                        # (it cannot read Thai). Bounded: OCR takes seconds.
                        if not is_ocr_text_coherent(page.text):
                            fallback = await self._rapid_fallback(img)
                            if fallback is not None:
                                logger.info(
                                    "OCR fallback: rapidocr replaced incoherent "
                                    "tesseract text (%d chars)", len(page.text))
                                page.text = fallback
                                page.blocks = []
                    page.seconds = time.perf_counter() - ocr_start
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    page.error = f"OCR failed: {exc}"
                    page.seconds = time.perf_counter() - started
        finally:
            doc.close()
        if self.settings.ocr_cache_enabled and all(not p.error for p in self.last_pages):
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

    async def _rapid_fallback(self, img: Image.Image) -> str | None:
        """One-shot RapidOCR retry for incoherent Tesseract output.

        Returns the RapidOCR text only when it is coherent itself;
        otherwise None (caller keeps the original text and the pipeline's
        coherence gate reports it honestly). Never raises.
        """
        try:
            raw = io.BytesIO()
            img.save(raw, format="PNG")
            text = await asyncio.wait_for(
                asyncio.to_thread(self._rapid._ocr_image_bytes, raw.getvalue()),
                self.settings.ocr_timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 — fallback must never break OCR
            logger.warning("OCR fallback failed: %s", exc)
            return None
        if text and is_ocr_text_coherent(text):
            return text
        return None

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
