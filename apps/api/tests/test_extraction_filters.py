"""Extraction filtering rules: placeholders dropped, junk never registered."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest  # noqa: E402

from app.services.field_catalog import (  # noqa: E402
    is_placeholder_value,
    is_registerable_new_field,
    is_sane_field_name,
)


@pytest.mark.parametrize("raw", ["N/A", "n/a", "NA", "-", "—", "null", "None", "", " ", "not available"])
def test_placeholders_detected(raw):
    assert is_placeholder_value(raw)


@pytest.mark.parametrize("raw", ["INV-001", "12.00", "RM", "27/03/2018"])
def test_real_values_not_placeholders(raw):
    assert not is_placeholder_value(raw)


def test_sane_names():
    assert is_sane_field_name("gst_summary")
    assert is_sane_field_name("amount_rm")
    assert not is_sane_field_name("amount_(rm)")  # parentheses stripped by normalize, but raw fails
    assert not is_sane_field_name("2much")
    assert not is_sane_field_name("has space")


def test_registerable_requires_real_value_and_confidence():
    assert is_registerable_new_field("loyalty_points", "120", 0.8)
    assert not is_registerable_new_field("gst_summary", "N/A", 0.9)  # placeholder value
    assert not is_registerable_new_field("amount_(rm)", "12", 0.9)  # junk name
    assert not is_registerable_new_field("weak_field", "x", 0.3)  # low confidence
