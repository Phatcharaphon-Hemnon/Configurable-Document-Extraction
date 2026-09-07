"""Tests for reasoning-model <think>...</think> stripping in _try_parse.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import BaseModel

_REPO_ROOT = Path(__file__).resolve().parents[2]
_API_ROOT = Path(__file__).resolve().parents[1]
for _p in (_REPO_ROOT, _API_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.services.client import Client, _strip_think_blocks


class _DummySchema(BaseModel):
    name: str
    value: int


def test_strip_think_block_prefix():
    raw = "<think>Let me look at the fields. {\"note\": \"draft\"}</think>{\"name\": \"x\", \"value\": 2}"
    assert _strip_think_blocks(raw) == "{\"name\": \"x\", \"value\": 2}"


def test_strip_multiple_think_blocks():
    raw = "<think>a</think>middle<think>b</think>{\"name\": \"x\", \"value\": 2}"
    # _strip_think_blocks removes only the think blocks; surrounding prose
    # ("middle") is left for _try_parse to ignore via {…} extraction.
    assert _strip_think_blocks(raw) == "middle{\"name\": \"x\", \"value\": 2}"


def test_strip_unclosed_think_tag_keeps_rest():
    raw = "<think>truncated reasoning {\"name\": \"x\", \"value\": 2}"
    assert _strip_think_blocks(raw) == "truncated reasoning {\"name\": \"x\", \"value\": 2}"


def test_strip_no_think_tags_unchanged():
    raw = "{\"name\": \"x\", \"value\": 2}"
    assert _strip_think_blocks(raw) == raw


def test_try_parse_think_prefixed_json():
    raw = "<think>reasoning here</think>\n{\"name\": \"total\", \"value\": 1070}"
    parsed = Client._try_parse(_DummySchema, raw)
    assert parsed is not None
    assert parsed.name == "total"
    assert parsed.value == 1070


def test_try_parse_ignores_braces_inside_think_block():
    raw = (
        "<think>candidate {\"name\": \"wrong\", \"value\": 1} discarded</think>"
        "{\"name\": \"right\", \"value\": 42}"
    )
    parsed = Client._try_parse(_DummySchema, raw)
    assert parsed is not None
    assert parsed.name == "right"
    assert parsed.value == 42
