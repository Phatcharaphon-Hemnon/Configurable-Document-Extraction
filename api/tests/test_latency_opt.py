"""Latency optimization regressions (isolated storage, no network/credentials).

Covers: no whole-job retry on internal TypeError (service + activity),
page-at-a-time OCR streaming (order, incremental last_pages, stubbed
aparse_file honored), first-page timing, Judge canonical prompt (single
value occurrence, no triple), prompt-bump cache invalidation, and
completed-page preservation after a later failure through the stream path.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.core.config import Settings  # noqa: E402
from app.schemas.documents import ExtractedField  # noqa: E402
from app.services.extraction_service import (  # noqa: E402
    DocumentExtractionService,
    UploadedFilePart,
)
from app.services.job_store import InMemoryJobStore  # noqa: E402


def _settings(tmp_path: Path, **overrides) -> Settings:
    s = Settings()
    s.knowledge_base_path = str(tmp_path / "kb")
    s.database_enabled = False
    s.source_storage_path = str(tmp_path / "sources")
    s.cache_path = str(tmp_path / "cache")
    s.ocr_cache_path = str(tmp_path / "cache" / "ocr-results")
    s.result_cache_enabled = True
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _result(kind: str, text: str):
    from app.schemas.documents import ExtractionResult

    return ExtractionResult(
        doc_type=kind,
        full_text=text,
        fields=[dict(name="id", value=text, source_span=text, confidence=0.8)],
        needs_review=True,
    )


# ------------------------------------------------------------------
# No whole-job retry on internal TypeError
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_job_does_not_retry_on_internal_typeerror(tmp_path):
    service = DocumentExtractionService.__new__(DocumentExtractionService)
    service.job_store = InMemoryJobStore()
    service._job_lock = asyncio.Lock()
    service.result_cache = None
    calls = 0

    async def boom(parts, job_id, **kwargs):
        nonlocal calls
        calls += 1
        raise TypeError("simulated internal bug")

    service.extract_group = boom
    job = service.job_store.create()
    await service.run_job(job.job_id, [])
    assert calls == 1  # exactly one attempt — no silent duplicate run
    assert service.job_store.get(job.job_id).status == "failed"


def test_extract_activity_has_no_broad_typeerror_fallback():
    import inspect

    from app.temporal import activities

    src = inspect.getsource(activities.extract_activity)
    assert "except TypeError" not in src


# ------------------------------------------------------------------
# Page-at-a-time OCR streaming
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aparse_pages_streams_in_order_with_incremental_state(tmp_path):
    import app.services.local_ocr as loc
    from app.services.local_ocr import LocalOCRClient

    s = _settings(tmp_path)
    s.ocr_cache_enabled = False
    client = LocalOCRClient(s)

    pages = []
    for i in range(3):
        img = Image.new("RGB", (200, 100), "white")
        pages.append(loc.LoadedPage(img, False, False))

    monkey_texts = iter(["alpha", "beta", "gamma"])

    async def fake_tesseract(img, **kwargs):
        from app.schemas.ocr import OCRBlock

        text = next(monkey_texts)
        return [OCRBlock(text=text, confidence=0.9, box=(0, 0, 10, 10))]

    client._tesseract = fake_tesseract
    orig_loader = loc.load_page_images
    loc.load_page_images = lambda data, filename=None, dpi=300: pages
    try:
        seen = []
        async for page in client.aparse_pages(b"fake", "three.png", use_cache=False):
            seen.append(page.text)
            # State grows incrementally: after the first yield only page 1 exists.
            assert len(client.last_pages) == len(seen)
        assert seen == ["alpha", "beta", "gamma"]
        assert [p.text for p in client.last_pages] == seen
    finally:
        loc.load_page_images = orig_loader


@pytest.mark.asyncio
async def test_stream_honors_stubbed_aparse_file(tmp_path):
    """Doubles patching aparse_file (side_effect lists) keep working."""
    service = DocumentExtractionService(settings=_settings(tmp_path))
    service.ocr.aparse_file = AsyncMock(return_value=["one", "two"])
    service._extract_one_page = AsyncMock(side_effect=[_result("invoice", "one"), _result("invoice", "two")])
    response = await service.extract_group([UploadedFilePart("a.png", None, b"x")])
    assert [d.full_text for d in response.documents] == ["one", "two"]
    assert response.timings.get("time_to_first_page", -1) >= 0
    assert response.timings["time_to_first_page"] <= response.timings["processing"]


@pytest.mark.asyncio
async def test_completed_pages_preserved_after_later_failure_via_stream(tmp_path):
    service = DocumentExtractionService(settings=_settings(tmp_path))
    service.ocr.aparse_file = AsyncMock(return_value=["good", "bad"])

    async def extract(filename, page_text, **kwargs):
        if page_text == "bad":
            raise RuntimeError("extractor blew up")
        return _result("invoice", page_text)

    service._extract_one_page = extract
    response = await service.extract_group([UploadedFilePart("a.png", None, b"x")])
    assert len(response.documents) == 2
    assert response.documents[0].full_text == "good" and not response.documents[0].error
    # The later failure becomes an honest error document; the completed page
    # keeps its source/preview and the first-page timing is recorded.
    assert response.documents[1].error and "extractor blew up" in response.documents[1].error
    assert response.documents[1].needs_review
    assert response.timings.get("time_to_first_page", -1) >= 0


# ------------------------------------------------------------------
# Judge canonical prompt
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_judge_prompt_is_canonical_without_repeated_values():

    from app.agents.judge import JudgeAgent
    from app.schemas.documents import ExtractedTable
    from app.schemas.llm_schemas import JudgeResponseSchema
    from app.services.client import ClientResult

    captured: dict = {}

    async def fake_generate(**kwargs):
        captured["prompt"] = kwargs.get("prompt", "")
        return ClientResult(
            parsed=JudgeResponseSchema(score=0.9, issues=[], notes="ok"),
            raw_text="{}",
            raw_response={},
            request_summary={},
        )

    agent = JudgeAgent(Settings(), client=MagicMock())
    agent._client.generate_structured = fake_generate
    fields = [
        ExtractedField(name="invoice_number", value="INV-001", confidence=0.95,
                       source_span="Invoice No: INV-001"),
    ]
    tables = [
        ExtractedTable.model_validate({
            "name": "line_items",
            "columns": [{"key": "desc", "label": "Description"}],
            "rows": [[{"column": "desc", "value": "Widget",
                       "confidence": 0.9, "source_span": "Widget"}]],
        })
    ]
    result = await agent.evaluate(fields=fields, source_text="Invoice No: INV-001\nWidget",
                                  tables=tables)
    assert result.score == 0.9
    prompt = captured["prompt"]
    assert "Canonical records" in prompt
    assert "Field provenance" not in prompt
    assert "Structured identifiers" not in prompt
    assert "Predicted fields:" not in prompt
    # One record line per value (previously the same value appeared in the
    # prediction JSON, a provenance line, AND an identifier line).
    assert prompt.count("- field:invoice_number | value=") == 1
    assert prompt.count("- cell:line_items/0/desc | value=") == 1
    # Evidence travels with the record it supports.
    assert '| span="Invoice No: INV-001"' in prompt
    # Stable ids for scalars and cells.
    assert "field:invoice_number" in prompt
    assert "cell:line_items/0/desc" in prompt


@pytest.mark.asyncio
async def test_judge_still_rejects_unknown_field_issues():
    from app.agents.judge import JudgeAgent
    from app.schemas.llm_schemas import JudgeIssueEntry, JudgeResponseSchema
    from app.services.client import ClientResult

    async def fake_generate(**kwargs):
        return ClientResult(
            parsed=JudgeResponseSchema(
                score=0.2,
                issues=[JudgeIssueEntry(field="ghost_field", message="bad", severity="error")],
                notes="",
            ),
            raw_text="{}",
            raw_response={},
            request_summary={},
        )

    agent = JudgeAgent(Settings(), client=MagicMock())
    agent._client.generate_structured = fake_generate
    fields = [ExtractedField(name="invoice_number", value="1", confidence=0.9, source_span="1")]
    from app.services.client import ClientError

    with pytest.raises(ClientError):
        await agent.evaluate(fields=fields, source_text="1")


# ------------------------------------------------------------------
# Prompt-bump invalidation (page entries + manifests)
# ------------------------------------------------------------------

def test_prompt_version_bump_invalidates_manifest_and_entries(tmp_path, monkeypatch):
    import app.services.result_cache as rc

    s = _settings(tmp_path)
    cache = rc.ResultCache(s)
    assert cache.enabled_and_ready
    key_before = cache.manifest_key(file_bytes=b"data", filename="a.png", page_count=1)
    fp_before = cache.config_fingerprint_dict()
    monkeypatch.setattr(rc, "PROMPT_VERSION", "prompts-TEST-bump")
    monkeypatch.setattr(rc, "JUDGE_VERSION", "judge-TEST-bump")
    key_after = cache.manifest_key(file_bytes=b"data", filename="a.png", page_count=1)
    fp_after = cache.config_fingerprint_dict()
    assert key_before != key_after  # manifest invalidated by the bump
    assert fp_before != fp_after  # page fingerprints invalidated too
    # A manifest written under the old key is unreachable under the new one.
    assert cache.manifest_get(key_after)[0] is None
