"""Contract tests: job-poll log filter, judge tolerance, extractor dense-line rule.

- `_QuietJobsPollFilter` must drop successful `GET /api/jobs/… 200` access
  lines (the status code is the last uvicorn arg — a `" 200 "` substring
  check never matches) while keeping errors and other routes.
- The judge prompt must allow value-in-span matches and cap their severity
  (alignment with the validator's overlap fallback for long spans).
- The extractor prompt must require exact full-token copies for IDs/dates
  on number-dense lines (no fragment concatenation).
"""

from __future__ import annotations

import inspect
import logging
import sys
from pathlib import Path

_REPOROOT = Path(__file__).resolve().parents[2]
if str(_REPOROOT) not in sys.path:
    sys.path.insert(0, str(_REPOROOT))

from app.agents import extractors as extractors_mod  # noqa: E402
from app.agents import judge as judge_mod  # noqa: E402
from app.core.security import check_evidence  # noqa: E402
from app.main import _QuietJobsPollFilter  # noqa: E402

_JOB = "/api/jobs/99317c87-d3de-4097-8c4b-43ef593e9063"

# Table-linearized OCR of purchase_orders_10248.pdf: headers print first,
# cell values after — so 'Order ID 10248' never occurs contiguously.
PO_OCR = (
    "Purchase Orders Order ID Order Date Customer Name "
    "10248 2016-07-04 Paul Henriot Products "
    "Product ID: Product: Quantity: Unit Price: "
    "11 Queso Cabrales 12 14 "
    "42 Singaporean Hokkien Fried Mee 10 9.8 "
    "72 Mozzarella di Giovanni 5 34.8 Page 1"
)


def _record(args: object) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, "test", 1, '%s - "%s %s %s" %s', args, None
    )


def _fallback_record(message: str) -> logging.LogRecord:
    record = logging.LogRecord("uvicorn.access", logging.INFO, "test", 1, "x", "x", None)
    record.args = None
    record.getMessage = lambda: message  # type: ignore[method-assign]
    return record


def test_filter_drops_successful_job_poll():
    filt = _QuietJobsPollFilter()
    record = _record(("127.0.0.1:33618", "GET", _JOB, "HTTP/1.1", 200))
    assert filt.filter(record) is False


def test_filter_drops_string_status_code():
    filt = _QuietJobsPollFilter()
    record = _record(("127.0.0.1:1", "GET", _JOB, "HTTP/1.1", "200"))
    assert filt.filter(record) is False


def test_filter_keeps_failed_job_poll():
    filt = _QuietJobsPollFilter()
    record = _record(("127.0.0.1:33618", "GET", _JOB, "HTTP/1.1", 404))
    assert filt.filter(record) is True


def test_filter_keeps_other_routes():
    filt = _QuietJobsPollFilter()
    record = _record(("127.0.0.1:33612", "POST", "/api/extract", "HTTP/1.1", 202))
    assert filt.filter(record) is True


def test_filter_fallback_format():
    filt = _QuietJobsPollFilter()
    ok_line = _fallback_record('127.0.0.1:59948 - "GET /api/jobs/abc HTTP/1.1" 200 OK')
    not_found = _fallback_record('127.0.0.1:59948 - "GET /api/jobs/abc HTTP/1.1" 404 Not Found')
    assert filt.filter(ok_line) is False
    assert filt.filter(not_found) is True


def test_judge_prompt_allows_value_in_span():
    src = inspect.getsource(judge_mod.JudgeAgent.evaluate)
    assert "substring of source_span" in src


def test_judge_prompt_caps_substring_severity():
    src = inspect.getsource(judge_mod.JudgeAgent.evaluate)
    assert "never error" in src


def test_extractor_prompt_requires_exact_tokens_for_ids_dates():
    rules = extractors_mod._COMMON_RULES.lower()
    assert "never concatenate fragments" in rules
    assert "plausible calendar date" in rules


def test_extractor_prompt_bans_label_glued_spans():
    rules = extractors_mod._COMMON_RULES
    assert "never prepend column headers or labels" in rules
    assert "NOT 'Order ID 10248'" in rules


def test_extractor_prompt_omit_applies_to_required_fields():
    rules = extractors_mod._COMMON_RULES
    assert "REQUIRED catalog fields too" in rules
    assert "never invent currency, totals, or IDs" in rules


def test_extractor_prompt_computed_total_rule():
    rules = extractors_mod._COMMON_RULES
    assert "amount = quantity x" in rules


def test_table_label_glued_span_fails():
    """purchase_orders_10248.pdf: 'Order ID 10248' is fabricated evidence
    (headers and values linearize on separate lines) even though 10248 is
    the correct value — the strict validator must still flag the span."""
    problem = check_evidence("po_number", "10248", "Order ID 10248", PO_OCR)
    assert problem is not None and "hallucination" in problem


def test_bare_cell_span_passes():
    """The exact cell quote the prompt now requires must verify cleanly."""
    assert check_evidence("po_number", "10248", "10248", PO_OCR) is None
    assert check_evidence("order_date", "2016-07-04", "2016-07-04", PO_OCR) is None
    assert check_evidence("supplier_name", "Paul Henriot", "Paul Henriot", PO_OCR) is None


def test_guessed_currency_span_fails():
    """No currency token on the document: the meta-commentary span and the
    guessed value must both fail evidence."""
    problem = check_evidence("currency", "USD", "No explicit currency found on document", PO_OCR)
    assert problem is not None and "hallucination" in problem
