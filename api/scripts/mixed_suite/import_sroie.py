"""SROIE importer: verified local copy -> isolated staging + manifest fragment.

Eval-local semantic mapping (documented here, applied at import/scoring time
only; production field catalogs are never modified):

- ``company`` -> ``seller_name`` (assumption: receipt merchant header)
- ``address``  -> ``seller_address`` (assumption: merchant address block)
- ``total``    -> ``total_amount`` (assumption: amount payable)
- ``date``     -> ``sroie_receipt_date`` (deliberately NOT ``invoice_date``:
  a point-of-sale receipt date must not silently become invoice-date ground
  truth; scored as its own date-typed scope)

Only the annotated four-field scope is scored (``annotation_scope``).
Pages are extraction-only (``routing_excluded=True``): receipt-only sources
must not become router ground truth.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .select import select_ids

SROIE_SELECTION_SEED = 20260915
SROIE_QUOTA = 8
EXPECTED_ENTITY_KEYS = {"company", "address", "date", "total"}

FIELD_MAP = {
    "company": "seller_name",
    "address": "seller_address",
    "total": "total_amount",
    "date": "sroie_receipt_date",
}

# Production-comparable scope: only these three have production-catalog
# counterparts. ``sroie_receipt_date`` is preserved in the manifest fields
# and entities JSON but recorded as unsupported (never a false negative).
SUPPORTED_FIELDS = ["seller_address", "seller_name", "total_amount"]
UNSUPPORTED_FIELDS = ["sroie_receipt_date"]

SOURCE_NOTE = (
    "Local prepared copy /home/phatcharaphon/dataset/SROIE2019 "
    "(LayoutLM-structured; 626 train triples, not the official 600; "
    "mirror github.com/zzzDavid/ICDAR-2019-SROIE claims corrected annotations; "
    "official RRC competition terms govern the images)."
)


def available_ids(train_dir: Path) -> list[str]:
    img, ent, box = (train_dir / "img", train_dir / "entities", train_dir / "box")
    ids = sorted(p.stem for p in img.glob("*.jpg"))
    return [i for i in ids
            if (ent / f"{i}.txt").exists() and (box / f"{i}.txt").exists()]


def import_sroie(train_dir: Path, staging: Path, seed: int = SROIE_SELECTION_SEED,
                 count: int = SROIE_QUOTA) -> dict:
    """Copy ``count`` selected receipts into staging; return fragment + ledger."""
    ids = available_ids(train_dir)
    selected, _ = select_ids(ids, count, seed)
    staging.mkdir(parents=True, exist_ok=True)
    files, invalid = [], []
    for sid in selected:
        try:
            raw_ent = json.loads((train_dir / "entities" / f"{sid}.txt").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            invalid.append({"source_id": sid, "reason": f"unreadable entities: {exc}"})
            continue
        if set(raw_ent) != EXPECTED_ENTITY_KEYS:
            invalid.append({"source_id": sid,
                            "reason": f"unexpected entity keys: {sorted(raw_ent)}"})
            continue
        src_img = train_dir / "img" / f"{sid}.jpg"
        data = src_img.read_bytes()
        staged_name = f"sroie_{sid}.jpg"
        (staging / staged_name).write_bytes(data)
        # Preserve original box annotation bytes unchanged alongside the image.
        shutil.copy2(train_dir / "box" / f"{sid}.txt", staging / f"sroie_{sid}.box.txt")
        # Preserve the original extraction-label JSON separately from the mapped manifest.
        shutil.copy2(train_dir / "entities" / f"{sid}.txt", staging / f"sroie_{sid}.entities.json")
        fields = {FIELD_MAP[k]: raw_ent[k] for k in sorted(EXPECTED_ENTITY_KEYS)}
        files.append({
            "filename": staged_name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "dataset": {
                "source": "sroie",
                "split": "train",
                "source_id": sid,
                "license": "ICDAR-2019-SROIE competition terms (images); check RRC",
                "provenance_url": "https://github.com/zzzDavid/ICDAR-2019-SROIE",
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
            },
            "pages": [{
                "page_number": 1,
                "doc_type": "invoice",
                "language": "en",
                "fields": fields,
                "tables": [],
                "excluded_fields": [],
                "annotation_scope": SUPPORTED_FIELDS,
                "unsupported_fields": UNSUPPORTED_FIELDS,
                "routing_excluded": True,
                "notes": ("SROIE scope: company/address/date/total only. "
                          "date kept as sroie_receipt_date, not invoice_date. "
                          + SOURCE_NOTE),
            }],
        })
    return {"files": files, "invalid": invalid,
            "selection": {"seed": seed, "pool": len(ids), "selected": selected,
                          "source_note": SOURCE_NOTE}}
