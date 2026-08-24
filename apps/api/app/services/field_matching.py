"""Shared field-name normalization and alternative-name lookup.

This module is the single source of truth for :func:`_normalize_name` and
provides :func:`build_alternative_name_lookup` to build a
``normalized_name → FieldDefinition`` dict from catalog fields, keyed by
both the canonical name and every ``alternative_names`` entry.

Used by ``router.py``, ``extractors.py``, and ``extraction_service.py``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.schemas.documents import FieldDefinition


def _normalize_name(name: str) -> str:
    """Normalize a field name for catalog-matching purposes.

    Rules (order matters):
    1. Strip leading/trailing whitespace.
    2. Lowercase.
    3. Replace any run of whitespace or hyphens with a single underscore.
    4. Collapse multiple consecutive underscores to one.

    This intentionally does NOT perform synonym mapping — "Merchant Name"
    and "vendor_name" will NOT match.  Only case/spacing differences are
    bridged.

    Examples::

        >>> _normalize_name("Total Amount")
        'total_amount'
        >>> _normalize_name("  Invoice-Number  ")
        'invoice_number'
        >>> _normalize_name("total_amount")
        'total_amount'
    """
    s = name.strip().lower()
    s = re.sub(r"[\s\-]+", "_", s)
    s = re.sub(r"_+", "_", s)
    return s


def build_alternative_name_lookup(
    catalog_fields: list[FieldDefinition],
) -> dict[str, FieldDefinition]:
    """Build a normalized-name → FieldDefinition lookup from catalog fields.

    Keys include both the canonical ``field.name`` and every entry in
    ``field.alternative_names``, all passed through :func:`_normalize_name`.
    If two fields map the same normalized key, the first one wins (should
    not happen with well-formed catalogs).

    Parameters
    ----------
    catalog_fields:
        List of :class:`FieldDefinition` objects, typically from
        ``KnowledgeBaseRepository.get_catalog_fields(doc_type)``.

    Returns
    -------
    dict[str, FieldDefinition]
        Mapping from every normalized name (canonical + alternatives) to
        its owning ``FieldDefinition``.
    """
    lookup: dict[str, FieldDefinition] = {}
    for cf in catalog_fields:
        norm = _normalize_name(cf.name)
        if norm not in lookup:
            lookup[norm] = cf
        for alt in cf.alternative_names:
            alt_norm = _normalize_name(alt)
            if alt_norm not in lookup:
                lookup[alt_norm] = cf
    return lookup


def _try_parse_number(v: Any) -> float | None:
    if v is None:
        return None

    if isinstance(v, (int, float)):
        return float(v)

    if isinstance(v, str):
        s = v.strip()
        for sym in ("$", "€", "฿"):
            s = s.replace(sym, "")
        s = s.replace(",", "")
        if s.startswith("(") and s.endswith(")"):
            s = "-" + s[1:-1]
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _try_parse_date(v: Any) -> datetime | None:
    from app.services.date_formats import KNOWN_DATE_FORMATS

    if not isinstance(v, str):
        return None
    s = v.strip()
    if not s:
        return None

    if re.fullmatch(r"\d+", s):
        return None

    for fmt in KNOWN_DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _try_parse_json_container(v: Any) -> Any | None:
    if isinstance(v, (list, dict)):
        return v
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        if not (s.startswith("[") or s.startswith("{")):
            return None
        try:
            return json.loads(s)
        except Exception:
            return None
    return None


def _normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())


def values_match(predicted: Any, expected: Any) -> bool:
    """Compare prediction vs ground-truth values with normalization.

    This affects only the *match decision* (precision/recall/F1 and
    mismatch classification), not the raw values shown in mismatches.
    """
    # a) Exact-match fast path
    try:
        if predicted == expected:
            return True
    except Exception:
        pass

    # b) None/missing handling
    if predicted is None or expected is None:
        return predicted is None and expected is None

    # c) Numeric-like comparison (with epsilon)
    pn = _try_parse_number(predicted)
    en = _try_parse_number(expected)
    if pn is not None and en is not None:
        return abs(pn - en) <= 1e-6

    # e) Date-like comparison
    pd = _try_parse_date(predicted)
    ed = _try_parse_date(expected)
    if pd is not None and ed is not None:
        return pd == ed

    # f) List/array comparison (JSON-encoded strings)
    p_container = _try_parse_json_container(predicted)
    e_container = _try_parse_json_container(expected)
    if p_container is not None and e_container is not None:
        if isinstance(p_container, list) and isinstance(e_container, list):
            if len(p_container) != len(e_container):
                return False
            return all(values_match(p_item, e_item) for p_item, e_item in zip(p_container, e_container))
        if isinstance(p_container, dict) and isinstance(e_container, dict):
            if p_container.keys() != e_container.keys():
                return False
            return all(values_match(p_container[k], e_container[k]) for k in e_container.keys())
        return False

    # d) String comparison
    if isinstance(predicted, str) and isinstance(expected, str):
        return _normalize_ws(predicted).lower() == _normalize_ws(expected).lower()

    # g) Fallback
    try:
        return predicted == expected
    except Exception:
        return False
