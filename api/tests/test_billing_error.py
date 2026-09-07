"""Tests for fail-fast handling of OpenAI billing/quota errors.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

_REPO_ROOT = Path(__file__).resolve().parents[2]
_API_ROOT = Path(__file__).resolve().parents[1]
for _p in (_REPO_ROOT, _API_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.core.config import Settings
from app.services.client import (
    Client,
    ClientError,
    _is_payment_error,
)

_BILLING_403 = (
    "Error code: 403 - {'error': {'code': 403, 'message': 'Billing account "
    "not configured. Enable billing at https://platform.openai.com/account/billing'}}"
)
_INSUFFICIENT_QUOTA = (
    "Error code: 429 - {'error': {'message': 'You exceeded your current quota, "
    "please check your plan and billing details.', 'type': 'insufficient_quota', "
    "'code': 'insufficient_quota'}}"
)
_PER_DAY_429 = (
    "Error code: 429 - {'error': {'message': 'Quota exceeded for metric "
    "GenerateRequestsPerDayPerProjectPerModel, limit 250 per day'}}"
)
_PER_MINUTE_429 = (
    "Error code: 429 - {'error': {'message': 'Quota exceeded for metric "
    "GenerateRequestsPerMinutePerProjectPerModel-FreeTier, retry in 6s'}}"
)


class _DummySchema(BaseModel):
    name: str


def _make_client():
    settings = MagicMock(spec=Settings)
    settings.llm_api_key = "test-token"
    settings.llm_base_url = "https://api.openai.com/v1"
    settings.llm_request_timeout_seconds = 90.0
    settings.disable_strict_json_schema = False
    return Client(settings)


def test_is_payment_error_markers():
    assert _is_payment_error(Exception(_BILLING_403))
    assert _is_payment_error(Exception(_INSUFFICIENT_QUOTA))
    assert _is_payment_error(Exception(_PER_DAY_429))
    assert _is_payment_error(Exception("403 Forbidden"))
    # Per-minute throttling must keep backing off, never fail fast — even
    # though the quota metric name contains "FreeTier".
    assert not _is_payment_error(Exception(_PER_MINUTE_429))
    assert not _is_payment_error(Exception("Error code: 429 - rate limit exceeded"))
    assert not _is_payment_error(Exception("connection reset by peer"))


@pytest.mark.anyio
async def test_billing_error_fails_fast_on_first_tier():
    """A 403 must raise immediately without burning the remaining tiers."""
    client = _make_client()
    call_count = 0

    async def mock_create(**kwargs):
        nonlocal call_count
        call_count += 1
        raise Exception(_BILLING_403)

    client._client.chat.completions.create = mock_create

    with pytest.raises(ClientError, match="billing/quota"):
        await client.generate_structured(
            model="gpt-5.4-mini",
            prompt="test",
            response_schema=_DummySchema,
        )
    assert call_count == 1


@pytest.mark.anyio
async def test_per_day_quota_fails_fast_without_backoff():
    """Daily-quota 429s must not burn backoff retries before failing."""
    client = _make_client()
    call_count = 0

    async def mock_create(**kwargs):
        nonlocal call_count
        call_count += 1
        raise Exception(_PER_DAY_429)

    client._client.chat.completions.create = mock_create

    with pytest.raises(ClientError, match="midnight Pacific"):
        await client.generate_structured(
            model="gpt-5.4-mini",
            prompt="test",
            response_schema=_DummySchema,
        )
    assert call_count == 1


@pytest.mark.anyio
async def test_insufficient_quota_fails_fast():
    """OpenAI insufficient_quota must fail fast without tier fallback."""
    client = _make_client()
    call_count = 0

    async def mock_create(**kwargs):
        nonlocal call_count
        call_count += 1
        raise Exception(_INSUFFICIENT_QUOTA)

    client._client.chat.completions.create = mock_create

    with pytest.raises(ClientError, match="billing/quota"):
        await client.generate_structured(
            model="gpt-5.4-mini",
            prompt="test",
            response_schema=_DummySchema,
        )
    assert call_count == 1


@pytest.mark.anyio
async def test_billing_error_message_points_to_billing_page():
    client = _make_client()

    async def mock_create(**kwargs):
        raise Exception(_BILLING_403)

    client._client.chat.completions.create = mock_create

    with pytest.raises(ClientError) as exc_info:
        await client.generate_text(model="gpt-5.4-mini", prompt="test")
    assert "https://platform.openai.com/account/billing" in str(exc_info.value)
