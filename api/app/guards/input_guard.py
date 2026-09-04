"""Input validation guards for file uploads and requests."""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

from fastapi import UploadFile

logger = logging.getLogger(__name__)

# Allowed MIME types for document processing
ALLOWED_MIME_TYPES: set[str] = {
    # Images
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/tiff",
    # Documents
    "application/pdf",
}

# Extension to MIME type mapping (fallback when magic detection unavailable)
EXTENSION_TO_MIME: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".pdf": "application/pdf",
}

# File size limits (in bytes)
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
MAX_IMAGE_SIZE = 20 * 1024 * 1024  # 20MB
MAX_PDF_SIZE = 50 * 1024 * 1024  # 50MB

# Image dimension limits
MAX_IMAGE_WIDTH = 10000
MAX_IMAGE_HEIGHT = 10000


class InputValidationError(Exception):
    """Raised when input validation fails."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def _detect_mime_type(content: bytes, filename: str | None) -> str | None:
    """Detect MIME type from content bytes and filename.

    Tries file-extension first, falls back to content sniffing.
    """
    # Try extension-based detection first
    if filename:
        ext = Path(filename).suffix.lower()
        if ext in EXTENSION_TO_MIME:
            return EXTENSION_TO_MIME[ext]

    # Try content-based detection (magic bytes)
    try:
        # PDF magic bytes
        if content[:4] == b"%PDF":
            return "application/pdf"
        # JPEG magic bytes
        if content[:2] == b"\xff\xd8":
            return "image/jpeg"
        # PNG magic bytes
        if content[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        # GIF magic bytes
        if content[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif"
        # WebP magic bytes
        if content[:12] == b"RIFF" and content[8:12] == b"WEBP":
            return "image/webp"
        # BMP magic bytes
        if content[:2] == b"BM":
            return "image/bmp"
        # TIFF magic bytes
        if content[:2] in (b"II", b"MM"):
            return "image/tiff"
    except Exception:
        pass

    return None


def _get_image_dimensions(image_bytes: bytes, media_type: str) -> tuple[int, int] | None:
    """Extract image dimensions without PIL (lightweight check).

    Returns (width, height) or None if dimensions cannot be determined.
    """
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes))
        return img.size
    except Exception as e:
        logger.debug("Could not read image dimensions: %s", e)
        return None


async def validate_file_upload(
    file: UploadFile,
    *,
    content: bytes | None = None,
    max_file_size: int | None = None,
    max_image_size: int | None = None,
    max_pdf_size: int | None = None,
    check_dimensions: bool = True,
) -> None:
    """Validate an uploaded file before processing.

    Checks filename, file size, MIME type, and optionally image dimensions.

    Args:
        file: FastAPI UploadFile to validate.
        content: Optional pre-read file content to avoid reading the upload twice.
        max_file_size: Override maximum file size in bytes.
        max_image_size: Override maximum image size in bytes.
        max_pdf_size: Override maximum PDF size in bytes.
        check_dimensions: Whether to check image dimensions.

    Raises:
        InputValidationError: If any validation check fails.
    """
    max_file_size = max_file_size or MAX_FILE_SIZE
    max_image_size = max_image_size or MAX_IMAGE_SIZE
    max_pdf_size = max_pdf_size or MAX_PDF_SIZE

    # 1. Check filename
    if not file.filename:
        raise InputValidationError("No filename provided", "missing_filename")

    # 2. Read file content for validation
    read_from_file = content is None
    if read_from_file:
        content = await file.read()
    file_size = len(content)

    # 3. Check file size
    if file_size == 0:
        raise InputValidationError("File is empty", "empty_file")

    if file_size > max_file_size:
        raise InputValidationError(
            f"File too large: {file_size:,} bytes (max: {max_file_size:,})",
            "file_too_large",
        )

    # 4. Verify MIME type (not just extension)
    detected_mime = _detect_mime_type(content, file.filename)

    if detected_mime is None:
        raise InputValidationError(
            "Unable to determine file type. Please upload a valid image or PDF.",
            "unknown_file_type",
        )

    if detected_mime not in ALLOWED_MIME_TYPES:
        raise InputValidationError(
            f"Unsupported file type: {detected_mime}",
            "unsupported_file_type",
        )

    # 5. Check size by type
    if detected_mime.startswith("image/") and file_size > max_image_size:
        raise InputValidationError(
            f"Image too large: {file_size:,} bytes (max: {max_image_size:,})",
            "image_too_large",
        )

    if detected_mime == "application/pdf" and file_size > max_pdf_size:
        raise InputValidationError(
            f"PDF too large: {file_size:,} bytes (max: {max_pdf_size:,})",
            "pdf_too_large",
        )

    # 6. Check image dimensions
    if check_dimensions and detected_mime.startswith("image/"):
        dims = _get_image_dimensions(content, detected_mime)
        if dims:
            width, height = dims
            if width > MAX_IMAGE_WIDTH or height > MAX_IMAGE_HEIGHT:
                raise InputValidationError(
                    f"Image dimensions too large: {width}x{height} "
                    f"(max: {MAX_IMAGE_WIDTH}x{MAX_IMAGE_HEIGHT})",
                    "image_dimensions_too_large",
                )

    if read_from_file:
        await file.seek(0)

    logger.debug(
        "File validated: %s (%s, %d bytes)",
        file.filename,
        detected_mime,
        file_size,
    )


def validate_image_dimensions(
    image_bytes: bytes,
    media_type: str,
    *,
    max_width: int | None = None,
    max_height: int | None = None,
) -> tuple[int, int]:
    """Validate image dimensions and return (width, height).

    Args:
        image_bytes: Raw image bytes.
        media_type: Image MIME type.
        max_width: Override maximum width.
        max_height: Override maximum height.

    Returns:
        Tuple of (width, height).

    Raises:
        InputValidationError: If dimensions exceed limits or image is invalid.
    """
    max_width = max_width or MAX_IMAGE_WIDTH
    max_height = max_height or MAX_IMAGE_HEIGHT

    dims = _get_image_dimensions(image_bytes, media_type)
    if dims is None:
        raise InputValidationError(
            "Could not read image dimensions. File may be corrupted.",
            "invalid_image",
        )

    width, height = dims

    if width > max_width or height > max_height:
        raise InputValidationError(
            f"Image dimensions too large: {width}x{height} "
            f"(max: {max_width}x{max_height})",
            "image_dimensions_too_large",
        )

    return width, height
