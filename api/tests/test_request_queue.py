"""Regression coverage for bounded generation retries and FIFO job admission."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import AuthenticationError, BadRequestError, RateLimitError
from pydantic import BaseModel

from app.guards.timeout_guard import StageTimeoutConfig, TimeoutGuard
from app.services.client import Client, ClientError, _retry_after_seconds
from app.services.extraction_service import DocumentExtractionService, UploadedFilePart
from app.services.job_store import InMemoryJobStore, SQLiteJobStore


class Answer(BaseModel):
    value: str


def response(content='{"value":"ok"}'):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=None)


def error(kind=RateLimitError, status=429, message="too many concurrent requests", headers=None):
    return kind(message, response=httpx.Response(status, headers=headers,
                request=httpx.Request("POST", "https://test/v1")), body=None)


def client(create):
    value = Client.__new__(Client)
    value.settings = SimpleNamespace(llm_base_url="https://test/v1", llm_provider="ollama-cloud",
                                     llm_max_concurrent_requests=1, disable_strict_json_schema=False)
    value._timeout = 1
    value._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return value


async def generate(value):
    return await value.generate_structured(model="test", prompt="private document", response_schema=Answer,
                                           disable_reasoning=True)


@pytest.fixture
def fast_backoff(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("app.services.client.asyncio.sleep", sleep)
    monkeypatch.setattr("app.services.client.random.uniform", lambda *_: 0)
    return sleep


@pytest.mark.asyncio
async def test_persistent_429_never_changes_request(fast_backoff):
    create = AsyncMock(side_effect=error())
    with pytest.raises(ClientError, match="rate limit exhausted"):
        await generate(client(create))
    assert create.call_count == 4
    assert [call.args[0] for call in fast_backoff.call_args_list] == [3, 6, 12]
    requests = [call.kwargs for call in create.call_args_list]
    assert all(request == requests[0] for request in requests)
    assert "extra_body" in requests[0]


@pytest.mark.asyncio
async def test_retries_and_formats_share_one_budget(fast_backoff):
    create = AsyncMock(side_effect=[response("invalid"), error(), error(), response("invalid")])
    with pytest.raises(ClientError, match="budget exhausted"):
        await generate(client(create))
    assert create.call_count == 4


@pytest.mark.asyncio
async def test_retry_after_recovery_and_deadline(fast_backoff):
    create = AsyncMock(side_effect=[error(headers={"Retry-After": "20"}), response()])
    assert (await generate(client(create))).parsed.value == "ok"
    fast_backoff.assert_awaited_once_with(20)
    create = AsyncMock(side_effect=error(headers={"Retry-After": "200"}))
    async with TimeoutGuard(StageTimeoutConfig(extractor_seconds=150)).track("extractor"):
        with pytest.raises(ClientError, match="remaining stage deadline"):
            await generate(client(create))
    assert create.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [error(AuthenticationError, 401, "invalid api key"),
                                    error(message="weekly usage limit reached"),
                                    error(BadRequestError, 400, "invalid request")])
async def test_permanent_errors_fail_once(failure, fast_backoff):
    create = AsyncMock(side_effect=failure)
    with pytest.raises(ClientError):
        await generate(client(create))
    assert create.call_count == 1
    fast_backoff.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_unsupported_format_can_fall_back():
    create = AsyncMock(side_effect=[error(BadRequestError, 400, "json_schema not supported"), response()])
    assert (await generate(client(create))).parsed.value == "ok"
    assert [call.kwargs["response_format"]["type"] for call in create.call_args_list] == [
        "json_schema", "json_object"]
    assert all("extra_body" in call.kwargs for call in create.call_args_list)


@pytest.mark.asyncio
async def test_shared_limiter_across_clients_and_cancellation():
    entered = asyncio.Event()
    release = asyncio.Event()
    active = peak = 0

    async def create(**kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        entered.set()
        try:
            await release.wait()
            return response()
        finally:
            active -= 1

    clients = [client(create) for _ in range(4)]
    tasks = [asyncio.create_task(generate(value)) for value in clients]
    await entered.wait()
    await asyncio.sleep(0)
    assert active == peak == 1
    tasks[0].cancel()
    with pytest.raises(asyncio.CancelledError):
        await tasks[0]
    release.set()
    await asyncio.gather(*tasks[1:])
    assert peak == 1 and active == 0


@pytest.mark.asyncio
async def test_limiter_holds_slot_during_backoff(monkeypatch):
    backing_off, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def sleep(_):
        backing_off.set()
        await release.wait()

    monkeypatch.setattr("app.services.client.asyncio.sleep", sleep)

    async def first(**kwargs):
        calls.append("first")
        if len(calls) == 1:
            raise error()
        return response()

    async def second(**kwargs):
        calls.append("second")
        return response()

    task1 = asyncio.create_task(generate(client(first)))
    await backing_off.wait()
    task2 = asyncio.create_task(generate(client(second)))
    # A scheduling turn without patching sleep's event-loop behavior.
    tick = asyncio.Event()
    asyncio.get_running_loop().call_soon(tick.set)
    await tick.wait()
    assert calls == ["first"]
    release.set()
    await asyncio.gather(task1, task2)
    assert calls == ["first", "first", "second"]


@pytest.mark.asyncio
async def test_four_jobs_fifo_and_cancel_queued():
    service = DocumentExtractionService.__new__(DocumentExtractionService)
    service.job_store = InMemoryJobStore()
    service._job_lock = asyncio.Lock()
    entered, release = asyncio.Event(), asyncio.Event()
    jobs = [service.job_store.create() for _ in range(4)]
    order = []

    async def extract(parts, job_id):
        order.append(job_id)
        entered.set()
        await release.wait()
        service.job_store.save_result(job_id, {"documents": [{}]})

    service.extract_group = extract
    tasks = [asyncio.create_task(service.run_job(job.job_id, [])) for job in jobs]
    await entered.wait()
    assert [job.status for job in jobs] == ["processing", "queued", "queued", "queued"]
    tasks[1].cancel()
    with pytest.raises(asyncio.CancelledError):
        await tasks[1]
    assert jobs[1].status == "failed"
    release.set()
    await asyncio.gather(tasks[0], *tasks[2:])
    assert order == [jobs[index].job_id for index in (0, 2, 3)]
    assert all(jobs[index].status == "completed" for index in (0, 2, 3))


@pytest.mark.asyncio
async def test_queued_job_starts_after_first_fails():
    """A queued second upload must still run after the first job fails."""
    service = DocumentExtractionService.__new__(DocumentExtractionService)
    service.job_store = InMemoryJobStore()
    service._job_lock = asyncio.Lock()
    jobs = [service.job_store.create() for _ in range(2)]
    order = []

    async def extract(parts, job_id):
        order.append(job_id)
        if job_id == jobs[0].job_id:
            raise RuntimeError("boom")
        service.job_store.save_result(job_id, {"documents": [{}]})

    service.extract_group = extract
    await asyncio.gather(*(service.run_job(job.job_id, []) for job in jobs))
    assert order == [jobs[0].job_id, jobs[1].job_id]
    assert jobs[0].status == "failed"
    assert jobs[1].status == "completed"


def test_restart_cleans_processing_and_queued(tmp_path):
    store = SQLiteJobStore(str(tmp_path / "queue.db"))
    jobs = [store.create() for _ in range(3)]
    store.mark_processing(jobs[0].job_id)
    store.save_result(jobs[2].job_id, {"documents": [{"fields": []}]})
    assert store.fail_stale_queued() == 2
    assert [store.get(job.job_id).status for job in jobs] == ["failed", "failed", "completed"]


def test_retry_after_date_and_invalid_values():
    assert _retry_after_seconds(error(headers={"Retry-After": "Wed, 21 Oct 2099 07:28:00 GMT"})) > 0
    for header in ("oops", "nan", "inf", "-1"):
        assert _retry_after_seconds(error(headers={"Retry-After": header})) is None


@pytest.mark.asyncio
async def test_pages_are_sequential_and_ordered():
    from app.core.config import Settings
    service = DocumentExtractionService(Settings())
    service.job_store = InMemoryJobStore()
    service.ocr = SimpleNamespace(aparse_file=AsyncMock(return_value=["page1", "page2", "page3"]))
    order = []
    active = peak = 0
    from app.schemas.documents import ExtractionResult

    async def extract(**kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        order.append(kwargs["page_text"])
        active -= 1
        return ExtractionResult(doc_type="invoice", fields=[], validation_errors=[], needs_review=True,
                                full_text=kwargs["page_text"])

    service._extract_one_page = extract
    result = await service.extract_group([UploadedFilePart("a.pdf", "application/pdf", b"pdf")])
    assert peak == 1
    assert order == ["page1", "page2", "page3"]
    assert [doc.full_text for doc in result.documents] == order


@pytest.mark.asyncio
async def test_queue_wait_does_not_consume_stage_deadline():
    service = DocumentExtractionService.__new__(DocumentExtractionService)
    service.job_store = InMemoryJobStore()
    service._job_lock = asyncio.Lock()
    guard = TimeoutGuard(StageTimeoutConfig(extractor_seconds=0.01))
    job = service.job_store.create()

    async def extract(parts, job_id):
        async with guard.track("extractor"):
            service.job_store.save_result(job_id, {"documents": [{}]})

    service.extract_group = extract
    await service._job_lock.acquire()
    task = asyncio.create_task(service.run_job(job.job_id, []))
    try:
        await asyncio.sleep(0.03)
        assert job.status == "queued" and guard.get_timings() == {}
    finally:
        service._job_lock.release()
    await task
    assert job.status == "completed"


@pytest.mark.asyncio
async def test_duplicate_upload_reuses_waiting_job(monkeypatch):
    from starlette.requests import Request

    from app.api import routes

    service = DocumentExtractionService.__new__(DocumentExtractionService)
    service.job_store = InMemoryJobStore()
    service._job_lock = asyncio.Lock()
    service.extract_group = AsyncMock()
    monkeypatch.setattr(routes, "service", service)
    monkeypatch.setattr(routes, "settings", SimpleNamespace(guards_enabled=False))
    monkeypatch.setattr(routes, "_background_tasks", {})
    monkeypatch.setattr(routes, "_content_to_job", {})
    request = Request({"type": "http", "client": ("127.0.0.1", 123)})
    await service._job_lock.acquire()
    try:
        first = await routes.extract_document(request, [SimpleNamespace(filename="a.pdf", content_type="application/pdf", read=AsyncMock(return_value=b"same"))])
        second = await routes.extract_document(request, [SimpleNamespace(filename="a.pdf", content_type="application/pdf", read=AsyncMock(return_value=b"same"))])
        assert first.job_id == second.job_id
        assert len(routes._background_tasks) == 1
        service.extract_group.assert_not_awaited()
    finally:
        tasks = list(routes._background_tasks.values())
        # Allow run_job to enter its try/finally before cancelling it.
        await asyncio.sleep(0)
        for task in tasks:
            task.cancel()
        service._job_lock.release()
        await asyncio.gather(*tasks, return_exceptions=True)
