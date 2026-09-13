"""TrOCR selective retry for uncertain English line crops (CPU, lazy).

Uses ``microsoft/trocr-base-handwritten`` — a LINE recogniser. Never submit
full pages; only pre-cropped single-line images from hybrid detection.

* Lazy-loaded and reused (processor + model, CPU, eval mode, no-grad).
* Sequential CPU inference (one crop at a time, no batching).
* Generation scores are NEVER returned as extraction confidence — callers
  keep the original RapidOCR confidence and flag the page for review.
* Missing ``transformers``/``torch`` or model download failures never raise
  past the hybrid orchestrator: they become review reasons with RapidOCR
  text preserved (see ``hybrid_ocr.py``).

Checkpoint documentation: HuggingFace ``microsoft/trocr-base-handwritten``
(TrOCR base, fine-tuned on IAM handwriting; ViT encoder + RoBERTa decoder).
Pinned revision via ``HYBRID_TROCR_REVISION`` (default
``aff187bd81f8d73231cd3ed24b7857fcb10ae00e``). See
``docs/guides/thai_catalog_hybrid_ocr.md`` for provisioning/offline use.

Tested pins: ``transformers==4.55.4``, ``torch==2.8.0`` (CPU), ``pillow>=10``.
Install for hybrid only::

    pip install rapidocr==3.9.2 onnxruntime transformers torch pillow numpy
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_MODEL_DEFAULT = "microsoft/trocr-base-handwritten"


class TrOCRError(Exception):
    """Raised when TrOCR retry cannot run (missing deps, bad crop, ...)."""


class TrOCRClient:
    """Lazy, reused TrOCR line recogniser (CPU, sequential)."""

    def __init__(self, model_name: str = _MODEL_DEFAULT, revision: str | None = None) -> None:
        self.model_name = model_name or _MODEL_DEFAULT
        self.revision = revision or None
        self._processor: Any | None = None
        self._model: Any | None = None
        self._lock = threading.Lock()
        self._load_attempted = False
        self._load_error: str | None = None

    def is_available(self) -> bool:
        try:
            self._ensure_loaded()
            return True
        except TrOCRError as exc:
            logger.warning("TrOCR unavailable: %s", exc)
            return False

    def _ensure_loaded(self) -> tuple[Any, Any]:
        with self._lock:
            if self._processor is not None and self._model is not None:
                return self._processor, self._model
            if self._load_attempted and self._load_error:
                raise TrOCRError(self._load_error)
            self._load_attempted = True
            try:
                from transformers import TrOCRProcessor, VisionEncoderDecoderModel  # type: ignore
            except ImportError as exc:
                self._load_error = (
                    "transformers/torch not installed for TrOCR retry. Install with: "
                    "pip install transformers torch pillow (see docs/guides/thai_catalog_hybrid_ocr.md)"
                )
                raise TrOCRError(self._load_error) from exc
            try:
                import torch  # type: ignore  # noqa: F401 — availability check only
            except ImportError as exc:
                self._load_error = "torch is required for TrOCR retry: pip install torch"
                raise TrOCRError(self._load_error) from exc
            try:
                kwargs: dict[str, Any] = {"trust_remote_code": False}
                if self.revision:
                    kwargs["revision"] = self.revision
                processor = TrOCRProcessor.from_pretrained(self.model_name, **kwargs)
                model = VisionEncoderDecoderModel.from_pretrained(self.model_name, **kwargs)
                model.eval()
                # Force CPU — hybrid is CPU-only by design.
                try:
                    model.to("cpu")
                except Exception:
                    pass
                self._processor = processor
                self._model = model
                logger.info("TrOCR loaded: %s (rev=%s, cpu)", self.model_name, self.revision or "main")
                return processor, model
            except Exception as exc:
                self._load_error = f"Failed to load TrOCR {self.model_name}: {exc}"
                raise TrOCRError(self._load_error) from exc

    def transcribe_crops(self, crops: list[Any]) -> list[str]:
        """Transcribe line crops sequentially on CPU. Returns one string per crop.

        Empty string = no usable reading (caller preserves RapidOCR text).
        Never raises for per-crop failures — those crops return "".
        Raises TrOCRError only when the model itself cannot load.
        """
        if not crops:
            return []
        processor, model = self._ensure_loaded()
        try:
            import torch  # type: ignore
        except ImportError as exc:
            raise TrOCRError("torch required for TrOCR inference") from exc
        outputs: list[str] = []
        # Sequential, no batching, no-grad, CPU.
        for crop in crops:
            try:
                image = self._crop_to_pil(crop)
                if image is None:
                    outputs.append("")
                    continue
                pixel_values = processor(images=image, return_tensors="pt").pixel_values
                try:
                    pixel_values = pixel_values.to("cpu")
                except Exception:
                    pass
                with torch.no_grad():
                    generated = model.generate(pixel_values)
                text = processor.batch_decode(generated, skip_special_tokens=True)
                outputs.append(str(text[0]).strip() if text else "")
            except Exception as exc:  # noqa: BLE001 — per-crop failures are "".
                logger.warning("TrOCR crop failed: %s", exc)
                outputs.append("")
        return outputs

    @staticmethod
    def _crop_to_pil(crop: Any) -> Any | None:
        try:
            import numpy as np
            from PIL import Image
        except ImportError:
            return None
        try:
            if isinstance(crop, Image.Image):
                return crop.convert("RGB")
            arr = crop
            if not isinstance(arr, np.ndarray):
                return None
            if arr.size == 0:
                return None
            if arr.dtype != np.uint8:
                arr = arr.astype(np.uint8)
            if arr.ndim == 2:
                return Image.fromarray(arr).convert("RGB")
            if arr.ndim == 3 and arr.shape[2] == 3:
                return Image.fromarray(arr).convert("RGB")
            if arr.ndim == 3 and arr.shape[2] == 4:
                return Image.fromarray(arr).convert("RGB")
            return None
        except Exception:
            return None

    def model_revision_info(self) -> dict[str, str]:
        return {
            "trocr_model": self.model_name,
            "trocr_revision": self.revision or "main",
        }
