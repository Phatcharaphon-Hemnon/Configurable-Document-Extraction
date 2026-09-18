"""Dispatch observability + timeout/cancellation attribution regressions (offline).

Covers the Phases 1-4 defect set for the local extractor-timeout
investigation WITHOUT live inference (mocked transports/agents, temp
storage, never runtime DBs, never gold answers as prompt/OCR input):

- typed region outcome on every return path (incl. splitter-fallback unpack)
- request-local HTTP dispatch accounting at the transport boundary
  (success / timeout-retry / tier-fallback / corrective counts)
- ledger isolation across concurrent tasks
- full-page progress dispatches + per-attempt durations + diagnostics
- StageTimeoutError vs request-timeout vs CancelledError attribution
- failed extractions never cached as completed; legacy payloads load
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.core.config import Settings  # noqa: E402
from app.guards.timeout_guard import TimeoutGuard  # noqa: E402
from app.schemas.documents import (  # noqa: E402
    ExtractedField,
    ExtractionCallResult,
    ExtractionResult,
    JudgeResult,
    RegionPageOutcome,
    RoutingDecision,
)
from app.schemas.ocr import OCRBlock, OCRPage  # noqa: E402
from app.services import request_control as rc  # noqa: E402
from app.services.client import Client  # noqa: E402
from app.services.extraction_service import (  # noqa: E402
    DocumentExtractionService,
    UploadedFilePart,
    _provider_error_details,
)
from app.services.result_cache import is_cacheable_result  # noqa: E402

PAGE_TEXT = "\n".join([
    "ACME REPLAY CO",
    "TAX INVOICE No: INV-R1",
    "Total: 25.50",
])


class _DummySchema(BaseModel):
    name: str
    value: int


def _ok_response(content: str):
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = MagicMock()
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 5
    resp.usage.total_tokens = 15
    resp.model_dump = MagicMock(return_value={"id": "t"})
    return resp


def _client_settings(**overrides):
    s = MagicMock(spec=Settings)
    s.llm_api_key = "test-token"
    s.llm_base_url = "https://ledger.test.local/v1"
    s.llm_request_timeout_seconds = 90.0
    s.disable_strict_json_schema = False
    s.llm_max_concurrent_requests = 1
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _kb(tmp_path: Path):
    kb = tmp_path / "kb"
    (kb / "field_catalog").mkdir(parents=True, exist_ok=True)
    (kb / "field_catalog" / "invoice_fields.json").write_text(json.dumps({
        "doc_type": "invoice",
        "fields": [
            {"name": "invoice_number", "type": "string", "required": True},
            {"name": "total_amount", "type": "number", "required": True},
        ],
    }), encoding="utf-8")
    for name, req in (("po_fields", "po_number"), ("delivery_note_fields", "delivery_number")):
        (kb / "field_catalog" / f"{name}.json").write_text(json.dumps({
            "doc_type": "x", "fields": [{"name": req, "type": "string", "required": True}],
        }), encoding="utf-8")
    return kb


def _service_settings(tmp_path: Path, **overrides) -> Settings:
    s = Settings()
    s.knowledge_base_path = str(_kb(tmp_path))
    s.database_enabled = True
    s.database_path = str(tmp_path / "history.db")
    s.source_storage_path = str(tmp_path / "sources")
    s.cache_path = str(tmp_path / "cache")
    s.result_cache_enabled = True
    s.result_cache_ttl_seconds = 3600.0
    s.result_cache_max_entries = 64
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


# ------------------------------------------------------------------
# A. Typed region outcome
# ------------------------------------------------------------------

def _single_block_service(tmp_path: Path):
    service = DocumentExtractionService(settings=_service_settings(
        tmp_path, region_extraction_enabled=True))
    service.ocr = MagicMock()
    service.router = MagicMock()
    service.router.classify = AsyncMock(
        return_value=RoutingDecision(doc_type="invoice", confidence=0.9, reason="t"))
    service.judge = MagicMock()
    service.judge.evaluate = AsyncMock(return_value=JudgeResult(score=0.9, issues=[], notes="ok"))
    for ext in service.extractors.values():
        ext.extract_call = AsyncMock(return_value=ExtractionCallResult(
            doc_type="invoice", page_number=1,
            fields=[ExtractedField(name="invoice_number", value="INV-R1",
                                   confidence=0.95, source_span="TAX INVOICE No: INV-R1")],
            tables=[], new_field_names=[]))
    return service


@pytest.mark.asyncio
async def test_region_fallback_returns_typed_outcome(tmp_path):
    """Splitter fallback (single region) must return the typed 3-field
    outcome — the 4-tuple unpack defect surfaced as 'Page pipeline failed'."""
    service = _single_block_service(tmp_path)
    blocks = [OCRBlock(text="TAX INVOICE No: INV-R1", confidence=0.9,
                       box=(10, 10, 200, 12), block_id="only")]
    ocr_page = OCRPage(text="TAX INVOICE No: INV-R1", blocks=blocks)
    outcome = await service._extract_page_with_regions(
        job_id="job-1", filename="a.png", page_text="TAX INVOICE No: INV-R1",
        ocr_page=ocr_page, ocr_uncertain_page=False, doc_type=None, page_index=1,
        progress={"dispatches": 0}, cache_on=False, region_cache=False,
        job_started=0.0, source_key="a.png:abc",
    )
    assert isinstance(outcome, RegionPageOutcome)
    assert outcome.document.error is None
    assert outcome.complete_for_cache is True
    assert "region" in outcome.path_detail.lower() or "single" in outcome.path_detail.lower()


@pytest.mark.asyncio
async def test_region_all_failed_returns_typed_outcome(tmp_path):
    service = _single_block_service(tmp_path)
    from app.services import regions as _regions

    real_split = _regions.split_page
    service_ok = service
    _ = (real_split, service_ok)
    # Force the multi-region path with failing region calls via geometry
    # reuse: use two-table-ish blocks so split yields >= 2 regions.
    blocks = [
        OCRBlock(text="ACME REPLAY CO", confidence=0.9, box=(10, 10, 100, 12), block_id="h"),
        OCRBlock(text="Item", confidence=0.9, box=(10, 60, 60, 12), block_id="h1"),
        OCRBlock(text="Qty", confidence=0.9, box=(200, 60, 30, 12), block_id="h2"),
        OCRBlock(text="WIDGET-A", confidence=0.9, box=(10, 80, 60, 12), block_id="r1"),
        OCRBlock(text="2", confidence=0.9, box=(200, 80, 30, 12), block_id="q1"),
        OCRBlock(text="Total: 25.50", confidence=0.9, box=(10, 130, 80, 12), block_id="t"),
    ]
    text = "\n".join([PAGE_TEXT, "Item Qty", "WIDGET-A 2", "GADGET-B 1"])
    split = _regions.split_page(blocks, text, 1)
    if len(split.regions) < 2:
        pytest.skip("geometry yields single region on this splitter version")
    for ext in service.extractors.values():
        ext.extract_call = AsyncMock(side_effect=RuntimeError("region down"))
    ocr_page = OCRPage(text=text, blocks=blocks)
    outcome = await service._extract_page_with_regions(
        job_id="job-2", filename="a.png", page_text=text,
        ocr_page=ocr_page, ocr_uncertain_page=False, doc_type=None, page_index=1,
        progress={"dispatches": 0}, cache_on=False, region_cache=False,
        job_started=0.0, source_key="a.png:abc",
    )
    assert isinstance(outcome, RegionPageOutcome)
    assert outcome.document.failed_stage == "extractor"
    assert outcome.complete_for_cache is False


# ------------------------------------------------------------------
# B. Request-local dispatch ledger at the transport boundary
# ------------------------------------------------------------------

def _scripted_client(events, **kw):
    """Real Client whose SDK create() follows a script.

    Script items: ("ok", content) | ("timeout",) | ("bad_request", message).
    """
    from openai import APITimeoutError, BadRequestError

    client = Client(_client_settings(**kw))
    plan = list(events)
    calls: list[dict] = []

    async def fake_create(**kwargs):
        calls.append(kwargs)
        kind = plan.pop(0) if plan else ("ok", '{"name": "x", "value": 1}')
        if kind[0] == "ok":
            return _ok_response(kind[1])
        if kind[0] == "timeout":
            raise APITimeoutError(request=MagicMock())
        if kind[0] == "bad_request":
            resp = MagicMock()
            resp.status_code = 400
            raise BadRequestError(message=kind[1], response=resp, body=None)
        raise AssertionError(kind)

    client._client.chat.completions.create = fake_create
    return client, calls


@pytest.mark.asyncio
async def test_ledger_records_successful_dispatch():
    client, _ = _scripted_client([("ok", '{"name": "a", "value": 1}')])
    token = rc.stage_context.set("extractor")
    with rc.collect_dispatches() as ledger:
        result = await client.generate_structured(
            model="m", prompt="p", response_schema=_DummySchema)
    assert result.parsed is not None
    assert len(ledger) == 1
    assert ledger[0].purpose == "initial"
    assert ledger[0].outcome == "ok"
    assert ledger[0].duration_s >= 0
    summary = rc.summarize_dispatches(ledger, "extractor")
    rc.stage_context.reset(token)
    assert summary["dispatches"] == 1 and summary["retries"] == 0


@pytest.mark.asyncio
async def test_ledger_counts_timeout_retry():
    client, _ = _scripted_client(
        [("timeout",), ("ok", '{"name": "a", "value": 1}')])
    token = rc.stage_context.set("extractor")
    with rc.collect_dispatches() as ledger:
        result = await client.generate_structured(
            model="m", prompt="p", response_schema=_DummySchema)
    assert result.parsed is not None
    assert [e.outcome for e in ledger] == ["timeout", "ok"]
    summary = rc.summarize_dispatches(ledger, "extractor")
    rc.stage_context.reset(token)
    assert summary["dispatches"] == 2
    assert summary["retries"] == 1
    assert summary["timeouts"] == 1


@pytest.mark.asyncio
async def test_ledger_counts_tier_fallback_not_retry():
    client, _ = _scripted_client([
        ("bad_request", "response_format json_schema is not supported here"),
        ("ok", '{"name": "a", "value": 1}'),
    ])
    token = rc.stage_context.set("extractor")
    with rc.collect_dispatches() as ledger:
        result = await client.generate_structured(
            model="m", prompt="p", response_schema=_DummySchema)
    assert result.parsed is not None
    assert [e.purpose for e in ledger] == ["initial", "fallback"]
    summary = rc.summarize_dispatches(ledger, "extractor")
    rc.stage_context.reset(token)
    assert summary["fallbacks"] == 1
    assert summary["retries"] == 0


@pytest.mark.asyncio
async def test_ledger_counts_corrective_generation():
    client, _ = _scripted_client([
        ("ok", "not json at all"),
        ("ok", '{"name": "a", "value": 1}'),
    ])
    token = rc.stage_context.set("extractor")
    with rc.collect_dispatches() as ledger:
        result = await client.generate_structured(
            model="m", prompt="p", response_schema=_DummySchema)
    assert result.parsed is not None
    assert [e.purpose for e in ledger] == ["initial", "correction"]
    summary = rc.summarize_dispatches(ledger, "extractor")
    rc.stage_context.reset(token)
    assert summary["corrections"] == 1
    assert summary["retries"] == 0


@pytest.mark.asyncio
async def test_ledger_records_real_deadline_timeout(monkeypatch):
    """A real request-deadline expiry (wait_for/timeout path, not an SDK
    exception) must still record timeout dispatch events, including the
    timeout retry."""
    async def no_sleep(delay, **kwargs):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    client, _ = _scripted_client([])

    async def slow_create(**kwargs):
        await asyncio.Event().wait()  # never resolves; deadline must win
        return _ok_response('{"name": "x", "value": 1}')

    client._client.chat.completions.create = slow_create
    client._timeout = 0.2
    token = rc.stage_context.set("extractor")
    try:
        with rc.collect_dispatches() as ledger:
            with pytest.raises(Exception):
                await client.generate_structured(
                    model="m", prompt="p", response_schema=_DummySchema)
    finally:
        rc.stage_context.reset(token)
    assert len(ledger) == 2
    assert [e.outcome for e in ledger] == ["timeout", "timeout"]
    summary = rc.summarize_dispatches(ledger, "extractor")
    assert summary["dispatches"] == 2
    assert summary["retries"] == 1
    assert summary["timeouts"] == 2


@pytest.mark.asyncio
async def test_collector_events_survive_exception(monkeypatch):
    """Copies of a task ledger made for reporting must include events even
    when the wrapped call raises (finally-extend pattern used by the
    single-dispatch diagnostic script)."""
    async def no_sleep(delay, **kwargs):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    client, _ = _scripted_client([("timeout",), ("timeout",)])

    async def slow_create(**kwargs):
        await asyncio.Event().wait()  # never resolves; deadline must win
        return _ok_response('{"name": "x", "value": 1}')

    client._client.chat.completions.create = slow_create
    client._timeout = 0.2
    token = rc.stage_context.set("extractor")
    report: list = []
    try:
        with rc.collect_dispatches() as events:
            try:
                await client.generate_structured(
                    model="m", prompt="p", response_schema=_DummySchema)
            finally:
                report.extend(events)
    except Exception:  # noqa: BLE001
        pass
    finally:
        rc.stage_context.reset(token)
    assert len(report) == 2
    assert all(e.outcome == "timeout" for e in report)


@pytest.mark.asyncio
async def test_outer_cancel_recorded_as_cancelled_not_timeout():
    """Operator cancel during a dispatch must surface CancelledError (never
    a timeout), without a retry, and must not be counted as a timeout."""
    client, _ = _scripted_client([])

    async def slow_create(**kwargs):
        await asyncio.Event().wait()  # never resolves; deadline must win
        return _ok_response('{"name": "x", "value": 1}')

    client._client.chat.completions.create = slow_create
    client._timeout = 30.0
    token = rc.stage_context.set("extractor")
    try:
        with rc.collect_dispatches() as ledger:
            task = asyncio.ensure_future(client.generate_structured(
                model="m", prompt="p", response_schema=_DummySchema))
            await asyncio.sleep(0.1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    finally:
        rc.stage_context.reset(token)
    assert all(e.outcome != "timeout" for e in ledger)
    summary = rc.summarize_dispatches(ledger, "extractor")
    assert summary["timeouts"] == 0


@pytest.mark.asyncio
async def test_ledgers_isolated_across_concurrent_tasks():
    client, _ = _scripted_client([])

    async def run(n):
        with rc.collect_dispatches() as ledger:
            await asyncio.sleep(0.01 * n)
            await client.generate_structured(
                model="m", prompt="p", response_schema=_DummySchema)
            return len(ledger)

    counts = await asyncio.gather(run(1), run(2), run(3))
    assert counts == [1, 1, 1]


def test_record_without_ledger_is_noop():
    rc.record_dispatch(rc.DispatchEvent(
        stage="s", model="m", tier=None, purpose="initial",
        duration_s=0.1, outcome="ok"))


# ------------------------------------------------------------------
# C. Timeout vs cancellation attribution
# ------------------------------------------------------------------

def test_stage_timeout_gets_typed_details():
    from app.guards.timeout_guard import StageTimeoutError

    details = _provider_error_details(
        StageTimeoutError("extractor", 150.0),
        stage="extractor", model="m", provider="p")
    assert details is not None
    assert details.error_type == "StageTimeoutError"
    assert "150" in (details.message or "")


def test_cancelled_error_never_becomes_timeout_details():
    assert _provider_error_details(
        asyncio.CancelledError(), stage="extractor",
        model="m", provider="p") is None


@pytest.mark.asyncio
async def test_track_converts_outer_cancel_to_cancelled_not_stage_timeout():
    guard = TimeoutGuard()

    async def runner():
        async with guard.track("extractor"):
            await asyncio.sleep(60)

    task = asyncio.ensure_future(runner())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ------------------------------------------------------------------
# D. Failure semantics + legacy compatibility
# ------------------------------------------------------------------

def test_failed_extraction_not_cacheable():
    failed = ExtractionResult(
        doc_type="invoice", fields=[], needs_review=True,
        completeness_score=0.0, error="Extractor failed: LLM request timed out",
        failed_stage="extractor")
    assert failed.judge_status == "unavailable"
    ok, _reason = is_cacheable_result(failed)
    assert ok is False


def test_legacy_payload_without_diagnostics_loads():
    legacy = {
        "doc_type": "invoice",
        "fields": [{"name": "invoice_number", "value": "INV-1",
                    "confidence": 0.9, "source_span": "INV-1"}],
        "validation_errors": [], "needs_review": False,
        "timings": {"router": 1.0}, "usage": {"router": {"attempts": 1}},
    }
    doc = ExtractionResult.model_validate(legacy)
    assert doc.diagnostics == {}
    assert doc.model_dump(mode="json")["diagnostics"] == {}


# ------------------------------------------------------------------
# E. Full-page path publishes dispatches + durations + diagnostics
# ------------------------------------------------------------------

def _mock_transport_client(script, monkeypatch):
    import httpx

    requests: list = []
    plan = list(script)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        status, body = plan.pop(0) if plan else (200, {
            "id": "c", "object": "chat.completion", "created": 1,
            "model": "m", "choices": [{
                "index": 0, "message": {"role": "assistant", "content": "{}"},
                "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5,
                      "total_tokens": 10}})
        return httpx.Response(status, json=body)

    async def no_sleep(delay, **kwargs):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    settings = _client_settings()
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler))
    return Client(settings, http_client=http_client), requests


def _router_body():
    return {"doc_type": "invoice", "language": "en",
            "confidence": 0.95, "reason": "t"}


def _extractor_body():
    return {"fields": [
        {"name": "invoice_number", "value": "INV-R1",
         "confidence": 0.95, "source_span": "TAX INVOICE No: INV-R1"},
        {"name": "total_amount", "value": 25.50,
         "confidence": 0.9, "source_span": "Total: 25.50"}],
        "tables": []}


@pytest.mark.asyncio
async def test_full_page_publishes_dispatches_durations_diagnostics(
        tmp_path, monkeypatch):
    service = DocumentExtractionService(settings=_service_settings(tmp_path))
    service.ocr = MagicMock()
    service.ocr.aparse_file = AsyncMock(return_value=[PAGE_TEXT])
    service.ocr.last_pages = [OCRPage(text=PAGE_TEXT, blocks=[], preview=b"png")]
    service.ocr.model_hashes = {}
    service.ocr._hybrid_fingerprint = MagicMock(return_value={})

    def body(content: dict) -> tuple:
        return (200, {
            "id": "c", "object": "chat.completion", "created": 1,
            "model": "m", "choices": [{
                "index": 0,
                "message": {"role": "assistant",
                            "content": json.dumps(content)},
                "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5,
                      "total_tokens": 10}})

    router_client, _ = _mock_transport_client([body(_router_body())], monkeypatch)
    extractor_client, _ = _mock_transport_client(
        [body(_extractor_body())], monkeypatch)
    service.router._client = router_client
    service.extractors["invoice"]._client = extractor_client
    service.judge.evaluate = AsyncMock(
        return_value=JudgeResult(score=0.9, issues=[], notes="ok"))

    job = service.job_store.create(
        filename="a.png", content_type="image/png", size_bytes=3)
    await service.run_job(job.job_id, [UploadedFilePart("a.png", "image/png", b"img")])
    status = service.get_batch_status(job.job_id)
    assert status is not None and status.status == "completed"
    assert status.result is not None
    doc = status.result.documents[0]
    assert doc.error is None
    attempts = sum(u.get("attempts", 0) for u in doc.usage.values())
    assert attempts >= 2  # router + extractor dispatches counted
    assert status.result.timings.get("dispatches", 0) >= 2
    assert any(k.startswith("extractor_attempt_") for k in doc.timings)
    assert doc.diagnostics.get("extraction_path") == "full-page"
    assert isinstance(doc.diagnostics.get("text_chars"), int)
