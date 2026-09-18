"""DocILE importer (annotated-trainval only; token-gated upstream).

Status: BLOCKED — no download token or verified local copy is available.
This module implements the full import path against the documented schema
(per-field ``page`` (0-based), ``bbox`` [l,t,r,b] relative, ``fieldtype``,
``line_item_id``, ``text``?) plus fixture-mode for offline tests. Fixtures
are never counted as imported documents.

Rules enforced here:
- Only real annotated documents (annotated-trainval layout); synthetic and
  unlabeled subsets are rejected as ineligible for this quota.
- ``line_item_id`` groups fields within each side only; it is never treated
  as a shared cross-side row key (see scorer notes).
- Only semantically equivalent project fields are mapped, via an explicit
  eval-local map supplied at import time (default empty until the real
  schema is inspected). Everything else lands in ``unsupported_fieldtypes``.
- No flattened scalars from line-item groups; groups become table rows.
- No invented printed headers.
"""

from __future__ import annotations

from pathlib import Path

DOCILE_REQUIRED_SPLITS = ("annotated-trainval",)
DOCILE_EXCLUDED_SPLITS = ("synthetic", "unlabeled", "test")


def blocked_report(reason: str) -> dict:
    return {"files": [], "invalid": [],
            "blocked": {"source": "docile",
                        "reason": reason,
                        "note": "DocILE is not in the current 10 SROIE + 10 FUNSD suite quota.",
                        "required_access": "token from https://docile.rossum.ai/ "
                                           "(`./download_dataset.sh TOKEN annotated-trainval ...`)"}}


def locate_split(base: Path) -> Path | None:
    """Return the annotated-trainval root if present with real annotations."""
    for split in DOCILE_REQUIRED_SPLITS:
        cand = base / split
        if cand.is_dir() and any(cand.iterdir()):
            return cand
    return None


def adapt_fields(raw_fields: list[dict], field_map: dict[str, str]) -> tuple[dict, list[dict], list[str]]:
    """Split raw DocILE fields into mapped scalars, line-item groups, unsupported.

    Returns (scalar_fields, line_item_rows, unsupported_fieldtypes).
    ``line_item_rows`` are (line_item_id, {fieldtype: text}) groups preserved
    intact for the line-item adapter; never flattened into scalars here.
    """
    scalars: dict = {}
    groups: dict = {}
    unsupported: list[str] = []
    for f in raw_fields:
        ftype = f.get("fieldtype", "")
        if "line_item_id" in f and f["line_item_id"] is not None:
            groups.setdefault(f["line_item_id"], {})[ftype] = f.get("text")
            continue
        if ftype in field_map:
            scalars[field_map[ftype]] = f.get("text")
        elif ftype not in unsupported:
            unsupported.append(ftype)
    rows = [(lid, groups[lid]) for lid in sorted(groups)]
    return scalars, rows, sorted(unsupported)
