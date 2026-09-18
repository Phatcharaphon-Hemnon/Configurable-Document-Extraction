"""FUNSD importer: verified local copy -> isolated staging + manifest fragment.

Source (recorded correction): /home/phatcharaphon/Downloads/dataset/
(NOT ~/download/dataset, which does not exist). Official 149-pair train
split; test split never used (unannotated for our purposes).

Rules enforced here:
- QA pairs derive ONLY from explicit ``linking`` ids; unlinked entities and
  ambiguous/dangling links are recorded separately, never proximity-inferred,
  never forced one-to-one.
- Original PNG + JSON bytes preserved unchanged; link endpoints validated;
  repeated link declarations recorded with occurrence counts.
- No mapping to invoice/PO/DN fields, no fabricated table rows, no catalog
  writes. Pages carry document_kind="form" with null production type.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .select import select_ids

FUNSD_SELECTION_SEED = 20260915
FUNSD_QUOTA = 10
FUNSD_LABELS = {"header", "question", "answer", "other"}
SOURCE_NOTE = ("Local copy /home/phatcharaphon/Downloads/dataset/ "
               "(149 complete train pairs, official FUNSD layout).")


def available_ids(train_dir: Path) -> list[str]:
    imgs = {p.stem for p in (train_dir / "images").glob("*.png")}
    anns = {p.stem for p in (train_dir / "annotations").glob("*.json")}
    return sorted(imgs & anns)


def validate_annotation(raw: object) -> tuple[dict, list[str]]:
    """Validate one FUNSD annotation. Returns (derived, errors).

    ``derived`` holds entities, links (repeated flagged), unlinked_ids,
    ambiguous entries and QA pairs from explicit links only.
    """
    errors: list[str] = []
    if not isinstance(raw, dict) or not isinstance(raw.get("form"), list):
        return {}, ["top-level form list missing"]
    entities: list[dict] = []
    by_id: dict = {}
    for entry in raw["form"]:
        if not isinstance(entry, dict):
            errors.append("non-object form entry")
            continue
        for key in ("id", "text", "box", "label", "words", "linking"):
            if key not in entry:
                errors.append(f"entry missing {key}: {entry.get('id')!r}")
        if entry.get("label") not in FUNSD_LABELS:
            errors.append(f"unknown label: {entry.get('label')!r}")
        if not isinstance(entry.get("box"), list) or len(entry.get("box", [])) != 4:
            errors.append(f"bad box for id {entry.get('id')!r}")
        if not isinstance(entry.get("linking"), list):
            errors.append(f"bad linking for id {entry.get('id')!r}")
        entities.append({
            "id": entry.get("id"),
            "text": entry.get("text", ""),
            "box": entry.get("box", []),
            "label": entry.get("label", ""),
            "words": entry.get("words", []),
        })
        by_id[entry.get("id")] = entry
    link_counts: dict[tuple, int] = {}
    links: list[dict] = []
    ambiguous: list[dict] = []
    for entry in raw["form"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("linking"), list):
            continue
        for item in entry["linking"]:
            # This copy stores links as explicit [source, target] pairs;
            # a bare scalar keeps the legacy entry.id -> target reading.
            if isinstance(item, list):
                if len(item) != 2:
                    ambiguous.append({"from": entry.get("id"), "to": str(item),
                                      "reason": "malformed link pair"})
                    continue
                src_id, tgt_id = item
            else:
                src_id, tgt_id = entry.get("id"), item
            try:
                hash(src_id)
                hash(tgt_id)
            except TypeError:
                ambiguous.append({"from": str(src_id), "to": str(tgt_id),
                                  "reason": "non-scalar link endpoint"})
                continue
            if src_id not in by_id or tgt_id not in by_id:
                ambiguous.append({"from": src_id, "to": tgt_id,
                                  "reason": "dangling endpoint"})
                continue
            key = (src_id, tgt_id)
            link_counts[key] = link_counts.get(key, 0) + 1
    for (src_id, tgt_id), count in sorted(link_counts.items(), key=lambda kv: str(kv[0])):
        links.append({"source": src_id, "target": tgt_id, "repeated": count > 1,
                      "declarations": count})
    linked_ids = {lk["source"] for lk in links} | {lk["target"] for lk in links}
    unlinked = [e["id"] for e in entities if e["id"] not in linked_ids]
    qa_pairs = [{"from": lk["source"], "to": lk["target"],
                 "from_text": by_id[lk["source"]].get("text", ""),
                 "to_text": by_id[lk["target"]].get("text", "")} for lk in links]
    derived = {"entities": entities, "links": links, "unlinked_ids": unlinked,
               "ambiguous": ambiguous, "qa_pairs": qa_pairs}
    # Ambiguous links are recorded, not invalidating; structural problems above are.
    return derived, errors


def select_valid_ids(sorted_ids: list[str], count: int, seed: int,
                     train_dir: Path) -> tuple[list[str], list[dict]]:
    """Deterministic selection skipping invalid annotations (recorded)."""
    selected, _ = select_ids(sorted_ids, count, seed)
    valid: list[str] = []
    invalid: list[dict] = []
    pool = [i for i in sorted_ids if i not in selected]
    candidates = list(selected)
    seed_extra = seed
    while len(valid) < count and candidates:
        sid = candidates.pop(0)
        try:
            raw = json.loads((train_dir / "annotations" / f"{sid}.json").read_text())
        except (OSError, json.JSONDecodeError) as exc:
            invalid.append({"source_id": sid, "reason": f"unreadable: {exc}"})
        else:
            _, errors = validate_annotation(raw)
            if errors:
                invalid.append({"source_id": sid, "reason": "; ".join(errors)})
            else:
                valid.append(sid)
                continue
        if len(valid) + len(candidates) < count:
            extra, _ = select_ids(pool, 1, seed_extra)
            seed_extra += 1
            if extra and extra[0] not in invalid and extra[0] not in valid:
                candidates.append(extra[0])
                pool = [i for i in pool if i != extra[0]]
    return sorted(valid), invalid


def import_funsd(train_dir: Path, staging: Path, seed: int = FUNSD_SELECTION_SEED,
                 count: int = FUNSD_QUOTA,
                 preselected: list[str] | None = None) -> dict:
    """Copy selected FUNSD docs into staging; return fragment + ledger."""
    ids = available_ids(train_dir)
    if preselected is not None:
        selected, invalid = sorted(set(preselected)), []
    else:
        selected, invalid = select_valid_ids(ids, count, seed, train_dir)
    staging.mkdir(parents=True, exist_ok=True)
    files = []
    for sid in selected:
        img_data = (train_dir / "images" / f"{sid}.png").read_bytes()
        ann_data = (train_dir / "annotations" / f"{sid}.json").read_bytes()
        (staging / f"funsd_{sid}.png").write_bytes(img_data)
        (staging / f"funsd_{sid}.json").write_bytes(ann_data)
        derived, _ = validate_annotation(json.loads(ann_data.decode("utf-8")))
        derived["source_sha256"] = hashlib.sha256(ann_data).hexdigest()
        (staging / f"funsd_{sid}.derived.json").write_text(
            json.dumps(derived, ensure_ascii=False, indent=2), encoding="utf-8")
        files.append({
            "filename": f"funsd_{sid}.png",
            "sha256": hashlib.sha256(img_data).hexdigest(),
            "dataset": {
                "source": "funsd",
                "split": "train",
                "source_id": sid,
                "license": "FUNSD (Vu et al.); check https://guillaumejaume.github.io/FUNSD/",
                "provenance_url": "https://guillaumejaume.github.io/FUNSD/",
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
            },
            "pages": [{
                "page_number": 1,
                "doc_type": None,
                "document_kind": "form",
                "production_doc_type": None,
                "language": "en",
                "fields": {},
                "tables": [],
                "excluded_fields": [],
                "annotation_scope": [],
                "unsupported_fields": [],
                "routing_excluded": True,
                "annotation_refs": [
                    {"path": f"funsd_{sid}.json",
                     "sha256": hashlib.sha256(ann_data).hexdigest(),
                     "kind": "funsd-original"},
                    {"path": f"funsd_{sid}.derived.json",
                     "sha256": hashlib.sha256(
                         (staging / f"funsd_{sid}.derived.json").read_bytes()).hexdigest(),
                     "kind": "funsd-derived"},
                ],
                "notes": ("FUNSD form: extraction/evaluation unsupported for "
                          "production tasks (entity/relationship review only). " + SOURCE_NOTE),
            }],
        })
    return {"files": files, "invalid": invalid,
            "selection": {"seed": seed, "pool": len(ids), "selected": selected,
                          "source_note": SOURCE_NOTE}}
