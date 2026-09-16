"""Timeout budget: request vs stage limits, startup warning, retry room.

45s request + 2s backoff + 45s retry ~= 92s fits inside 100s Router/Judge
(Extractor 150s allows the same retry plus the corrective generation).
A request timeout above a stage limit cancels before the retry can run.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

from app.core.config import validate_timeout_config


def _settings(request=45.0, router=100.0, extractor=150.0, judge=100.0):
    return SimpleNamespace(
        llm_request_timeout_seconds=request,
        router_timeout_seconds=router,
        extractor_timeout_seconds=extractor,
        judge_timeout_seconds=judge,
    )


def test_default_budget_leaves_retry_room():
    assert validate_timeout_config(_settings()) == []


def test_request_above_stage_warns():
    warnings = validate_timeout_config(_settings(request=1000.0, router=200.0))
    assert any("exceeds" in w and "ROUTER" in w for w in warnings)
    # 1000s also exceeds the 100s judge limit
    assert any("JUDGE" in w for w in warnings)


def test_request_without_retry_room_warns_but_single_attempt_ok():
    # 60s request: 60+2+60=122 > 100s router — single attempts run, no retry room.
    warnings = validate_timeout_config(_settings(request=60.0))
    assert any("no room" in w and "router" in w for w in warnings)


def test_startup_warning_logs_without_content_or_credentials(caplog):
    settings = _settings(request=1000.0, router=200.0)
    with caplog.at_level(logging.WARNING, logger="app.core.config"):
        validate_timeout_config(settings)
    assert caplog.records
    for record in caplog.records:
        text = record.getMessage()
        assert "nvapi" not in text.lower()
        assert "invoice" not in text.lower() or "ROUTER" in text
