"""Wrong response shapes must not become empty successful extractions."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.agents.judge import JudgeAgent
from app.schemas.documents import ExtractedField
from app.schemas.llm_schemas import ExtractionResponseSchema, JudgeResponseSchema
from app.services.client import Client, ClientError


def completion(payload):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))], usage=None)


def make_client(payloads, disable_strict=False):
    client = Client.__new__(Client)
    client.settings = SimpleNamespace(llm_base_url="https://test.invalid/v1", disable_strict_json_schema=disable_strict)
    client._timeout = 1
    create = AsyncMock(side_effect=[completion(payload) for payload in payloads])
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return client, create


@pytest.mark.parametrize("payload", [{}, {"score": 0}, {"fields": [], "score": 0}])
def test_unrelated_json_is_not_an_extraction(payload):
    with pytest.raises(ValidationError):
        ExtractionResponseSchema.model_validate(payload)


def test_explicit_empty_fields_is_valid_contract():
    assert ExtractionResponseSchema.model_validate({"fields": []}).fields == []


@pytest.mark.asyncio
@pytest.mark.parametrize("disable_strict", [False, True])
async def test_every_mode_has_schema_and_recovers_from_wrong_shape(disable_strict):
    valid = {"fields": [{"name": "bill_no", "value": "V001-540338", "confidence": 0.95,
                         "source_span": "Bill#: V001-540338"}]}
    client, create = make_client([{"score": 0}, valid], disable_strict)
    result = await client.generate_structured(model="test", prompt="Bill#: V001-540338", response_schema=ExtractionResponseSchema)
    assert result.parsed.fields[0].value == "V001-540338"
    assert create.call_count == 2
    for call in create.call_args_list:
        prompt = str(call.kwargs["messages"])
        assert '"fields"' in prompt and '"source_span"' in prompt
        assert "Bill#: V001-540338" in prompt


@pytest.mark.asyncio
async def test_wrong_shapes_exhaust_formats_as_failure():
    client, create = make_client([{"score": 0}] * 3)
    with pytest.raises(ClientError, match="unparseable JSON"):
        await client.generate_structured(model="test", prompt="Bill#: V001-540338", response_schema=ExtractionResponseSchema)
    assert create.call_count == 3


@pytest.mark.asyncio
async def test_image_regeneration_keeps_image_and_task():
    client, create = make_client([{"score": 0}, {"score": 0}, {"fields": []}])
    await client.generate_structured_with_image(model="test", prompt="Original extraction task", image_bytes=b"image",
                                               image_media_type="image/png", response_schema=ExtractionResponseSchema)
    for call in create.call_args_list:
        content = call.kwargs["messages"][0]["content"]
        assert any(part["type"] == "image_url" for part in content)
        assert "Original extraction task" in str(content) and '"fields"' in str(content)


@pytest.mark.asyncio
async def test_empty_prediction_does_not_call_judge_model():
    client = SimpleNamespace(generate_structured=AsyncMock())
    judge = JudgeAgent(SimpleNamespace(judge_model_name="test"), client=client)
    result = await judge.evaluate([], source_text="TOTAL 35.00")
    assert result.score == 0 and result.issues == []
    client.generate_structured.assert_not_awaited()


@pytest.mark.asyncio
async def test_judge_rejects_invented_field_issue():
    parsed = JudgeResponseSchema(score=0, issues=[{"field": "score", "message": "hallucinated", "severity": "error"}])
    client = SimpleNamespace(generate_structured=AsyncMock(return_value=SimpleNamespace(parsed=parsed)))
    judge = JudgeAgent(SimpleNamespace(judge_model_name="test"), client=client)
    fields = [ExtractedField(name="total", value=35, confidence=0.9, source_span="TOTAL 35.00")]
    with pytest.raises(ClientError, match="absent from the prediction"):
        await judge.evaluate(fields, source_text="TOTAL 35.00")
