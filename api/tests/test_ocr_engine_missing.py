"""Missing Tesseract binary fails visibly, never silently (doubles only)."""

import asyncio
import io
import logging

import pytest
from PIL import Image

from app.core.config import Settings
from app.services.local_ocr import LocalOCRClient, _describe_missing_binary


def _image() -> bytes:
    data = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(data, format="PNG")
    return data.getvalue()


def _settings(**overrides) -> Settings:
    settings = Settings()
    settings.ocr_cache_enabled = False
    settings.ocr_engine = "tesseract"
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


@pytest.mark.asyncio
async def test_missing_tesseract_binary_fails_page_visibly(monkeypatch):
    async def _missing(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "tesseract")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _missing)
    client = LocalOCRClient(_settings())
    pages = [p async for p in client.aparse_pages(_image(), "sroie.png")]
    assert len(pages) == 1
    page = pages[0]
    assert page.error and "OCR engine binary missing" in page.error
    assert ".local/ocr" in page.error
    assert page.text == ""
    assert page.engines_used == []


def test_missing_tesseract_binary_logs_startup_warning(monkeypatch, caplog):
    monkeypatch.setenv("TESSERACT_CMD", "/nonexistent/tesseract-missing")
    settings = Settings()
    settings.ocr_cache_enabled = False
    settings.ocr_engine = "tesseract"
    with caplog.at_level(logging.ERROR, logger="app.services.local_ocr"):
        LocalOCRClient(settings)
    assert "Tesseract binary missing" in caplog.text
    assert ".local/ocr" in caplog.text or "multilingual_ocr" in caplog.text


def test_missing_binary_message_names_bare_command_search_path(monkeypatch):
    # A bare "tesseract" must report the searched PATH, not just echo config.
    monkeypatch.setenv("PATH", "/nonexistent-bin")
    settings = _settings(tesseract_cmd="tesseract")
    client = LocalOCRClient(settings)
    assert "searched PATH=/nonexistent-bin" in _describe_missing_binary("tesseract")
    assert client.settings.tesseract_cmd == "tesseract"


@pytest.mark.asyncio
async def test_missing_binary_page_error_names_search_path(monkeypatch):
    # End-to-end of the message path: spawn fails -> page.error is actionable.
    async def _missing(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "tesseract")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _missing)
    monkeypatch.setenv("PATH", "/nonexistent-bin")
    client = LocalOCRClient(_settings(tesseract_cmd="tesseract"))
    pages = [p async for p in client.aparse_pages(_image(), "sroie.png")]
    assert len(pages) == 1
    assert pages[0].error and "searched PATH=/nonexistent-bin" in pages[0].error


def test_describe_missing_binary_absolute_path():
    hint = _describe_missing_binary("/nonexistent/tesseract-missing")
    assert "absolute path" in hint
    assert "/nonexistent/tesseract-missing" in hint
