"""Combined-manifest builder + readiness ledger (staging only).

Readiness is four separate counts (correction 5):
- imported documents (target 20 = 8 + 8 + 4)
- text-field-scoreable documents (non-empty field scope)
- line-item-scoreable documents (line-item groups with a compatible adapter)
- localization-only documents (boxes, no text scope)

Box-only documents are never presented as extraction examples.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def readiness(fragments: list[dict]) -> dict:
    files = [f for frag in fragments for f in frag.get("files", [])]
    by_source: dict[str, int] = {}
    text_scoreable = lineitem_scoreable = localization_only = 0
    supported_refs = unsupported_refs = 0
    for f in files:
        src = (f.get("dataset") or {}).get("source", "unknown")
        by_source[src] = by_source.get(src, 0) + 1
        scopes = [p.get("annotation_scope") or [] for p in f["pages"]]
        has_fields = any(scopes)
        has_lineitems = any((p.get("line_items") or []) for p in f["pages"])
        if has_fields:
            text_scoreable += 1
        if has_lineitems:
            lineitem_scoreable += 1
        if not has_fields and not has_lineitems:
            localization_only += 1
        for p in f["pages"]:
            supported_refs += len(p.get("annotation_scope") or [])
            unsupported_refs += len(p.get("unsupported_fields") or [])
    return {"imported": len(files), "by_source": by_source,
            "text_scoreable": text_scoreable,
            "lineitem_scoreable": lineitem_scoreable,
            "localization_only": localization_only,
            "supported_refs": supported_refs,
            "unsupported_refs": unsupported_refs}


def build_combined_manifest(fragments: list[dict], staging: Path,
                            quotas: dict[str, int] | None = None,
                            asset_dirs: dict[str, Path] | None = None,
                            release_subset: list[str] | None = None) -> tuple[dict, dict]:
    """Assemble + verify the combined manifest. Returns (manifest, ledger).

    ``asset_dirs`` maps dataset source -> directory holding that source's
    staged files (defaults to ``staging`` for every source).
    Raises ValueError on duplicate filenames, hash mismatches, or quota gaps
    (quota gaps are reported in the ledger; callers decide activation).
    """
    quotas = quotas or {"sroie": 8, "docile": 8, "thai_receipt": 4}
    asset_dirs = asset_dirs or {}
    files = [f for frag in fragments for f in frag.get("files", [])]
    seen: set[str] = set()
    for f in files:
        if f["filename"] in seen:
            raise ValueError(f"duplicate filename: {f['filename']}")
        seen.add(f["filename"])
        src = (f.get("dataset") or {}).get("source", "")
        data = (asset_dirs.get(src, staging) / f["filename"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != f["sha256"]:
            raise ValueError(f"hash mismatch after staging: {f['filename']}")
    led = readiness(fragments)
    led["quotas"] = quotas
    led["complete"] = all(led["by_source"].get(k, 0) >= v for k, v in quotas.items())
    manifest = {
        "version": 2,
        "annotation_method": ("Mixed external suite (SROIE/DocILE/Thai Receipt), "
                              "original annotations preserved; adapters eval-local. "
                              "AI-assisted/provisional, not human-adjudicated."),
        "release_subset": (release_subset if release_subset is not None
                           else sorted(f["filename"] for f in files)),
        "files": [{k: f[k] for k in ("filename", "sha256", "pages", "dataset")} for f in files],
    }
    return manifest, led


def write_staging_manifest(staging: Path, manifest: dict) -> Path:
    dest = staging / "combined_manifest.json"
    dest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest
