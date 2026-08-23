"""Shared field-name normalization and alternative-name lookup.

This module is the single source of truth for :func:`_normalize_name` and
provides :func:`build_alternative_name_lookup` to build a
``normalized_name → FieldDefinition`` dict from catalog fields, keyed by
both the canonical name and every ``alternative_names`` entry.

Used by ``router.py``, ``extractors.py``, and ``extraction_service.py``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

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
