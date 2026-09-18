"""Thai Receipt importer (Roboflow Universe, CC BY 4.0; auth-gated upstream).

Status: BLOCKED — public endpoints return 401/403 without credentials and no
authorized export is on hand. Implemented against the documented export shapes
(COCO-style JSON or YOLO txt + images) with fixture-mode for offline tests.

Rules enforced here:
- 4 distinct ORIGINAL images; augmented copies of the same image are excluded
  (grouped by recorded source hash / export metadata, never by guesswork).
- Box class labels are localization ground truth only. A box labeled
  ``total`` is NOT a transcription of its amount.
- Text extraction scores only where original text-value labels exist.
  Box-only documents become localization-only pages (empty extraction scope)
  plus a pending-transcription review item. Missing gold text is never
  generated via OCR or LLM.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def blocked_report(reason: str, observed: str) -> dict:
    return {"files": [], "invalid": [], "pending_transcription": [],
            "blocked": {"source": "thai_receipt", "quota": 4,
                        "reason": reason, "observed": observed,
                        "required_access": "Roboflow account + dataset export "
                                           "(https://universe.roboflow.com/test-b78az/ocr-receipt-thai)"}}


def dedupe_originals(candidates: list[dict]) -> tuple[list[dict], list[dict]]:
    """Keep one entry per distinct source image hash.

    Each candidate: {"image": bytes, "source_hash": str|None, "name": str}.
    Entries without a recorded source hash cannot prove distinctness and are
    reported as excluded (never silently kept).
    """
    seen: dict[str, dict] = {}
    excluded: list[dict] = []
    for c in candidates:
        key = c.get("source_hash") or hashlib.sha256(c["image"]).hexdigest()
        if c.get("source_hash") is None:
            excluded.append({"name": c["name"],
                             "reason": "no recorded source hash; distinctness unprovable"})
            continue
        if key in seen:
            excluded.append({"name": c["name"],
                             "reason": f"augmented/duplicate copy of {seen[key]['name']}"})
            continue
        seen[key] = c
    return list(seen.values()), excluded


def localization_only_page(filename: str, sha256: str, boxes: list[dict],
                            image_bytes: bytes) -> dict:
    """A box-only page: extraction scope empty, localization preserved."""
    return {
        "filename": filename,
        "sha256": sha256,
        "dataset": {
            "source": "thai_receipt",
            "split": "roboflow-export",
            "source_id": filename,
            "license": "CC BY 4.0",
            "provenance_url": "https://universe.roboflow.com/test-b78az/ocr-receipt-thai",
            "retrieved_at": "",
        },
        "pages": [{
            "page_number": 1,
            "doc_type": "invoice",
            "language": "th",
            "fields": {},
            "tables": [],
            "excluded_fields": [],
            "annotation_scope": [],
            "routing_excluded": True,
            "notes": ("Localization-only: box classes "
                      + ",".join(sorted({b.get('label', '?') for b in boxes}))
                      + "; no text-value labels, extraction unscored."),
        }],
        "boxes": boxes,
    }


def image_dest(staging: Path, filename: str, image_bytes: bytes) -> Path:
    dest = staging / filename
    dest.write_bytes(image_bytes)
    return dest
