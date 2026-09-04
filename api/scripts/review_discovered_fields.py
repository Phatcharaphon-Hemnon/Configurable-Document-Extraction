"""Review AI-discovered catalog fields for curation.

During extraction the pipeline auto-registers clearly labeled values that
have no catalog name (source="ai_discovered"). Across many receipts some of
those names are junk (OCR noise) or near-duplicates — this read-only script
lists them per document type and flags suspicious ones for manual cleanup.

Usage (from api/):
    source ../.venv/bin/activate
    python scripts/review_discovered_fields.py [--json]

To curate: edit api/app/data/knowledge_base/field_catalog/<type>_fields.json
directly — delete junk entries, fix type/required/validation_rule on the
keepers. The pipeline picks up edits automatically (mtime cache).
"""

from __future__ import annotations

import difflib
import json
import sys
from pathlib import Path

_CATALOG_FILES = {
    "invoice": "invoice_fields.json",
    "purchase_order": "po_fields.json",
    "delivery_note": "delivery_note_fields.json",
}

# Vague names that usually mean the LLM guessed instead of reading a label.
_GENERIC_NAMES = {
    "total", "amount", "value", "data", "info", "text", "number", "field",
    "item", "price", "date", "name", "address", "note", "detail", "misc",
}


def _catalog_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "app" / "data" / "knowledge_base" / "field_catalog"


def _norm(name: str) -> str:
    return "_".join("".join(c if c.isalnum() else "_" for c in name.lower()).split("_")).strip("_")


def _flag(name: str, known: set[str]) -> list[str]:
    flags: list[str] = []
    if len(name) > 30:
        flags.append("LONG>30")
    if any(ch.isdigit() for ch in name) and sum(c.isalpha() for c in name) < 3:
        flags.append("DIGITS")
    if name in _GENERIC_NAMES:
        flags.append("GENERIC")
    close = difflib.get_close_matches(name, sorted(known - {name}), n=1, cutoff=0.85)
    if close:
        flags.append(f"NEAR-DUP~{close[0]}")
    return flags


def collect(catalog_dir: Path | None = None) -> dict[str, list[dict]]:
    """Return {doc_type: [discovered field summaries]}."""
    base = catalog_dir or _catalog_dir()
    report: dict[str, list[dict]] = {}
    for doc_type, filename in _CATALOG_FILES.items():
        path = base / filename
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report[doc_type] = []
            continue
        entries = data.get("fields", []) if isinstance(data, dict) else []
        known = {_norm(str(e.get("name", ""))) for e in entries if isinstance(e, dict)}
        found = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("source", "catalog")) != "ai_discovered":
                continue
            name = str(entry.get("name", ""))
            found.append({
                "name": name,
                "type": entry.get("type", "string"),
                "required": bool(entry.get("required", False)),
                "validation_rule": entry.get("validation_rule", ""),
                "flags": _flag(_norm(name), known),
            })
        report[doc_type] = found
    return report


def main() -> None:
    report = collect()
    if "--json" in sys.argv[1:]:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    total = sum(len(v) for v in report.values())
    flagged = sum(1 for v in report.values() for f in v if f["flags"])
    print("AI-discovered catalog fields (source=ai_discovered)\n")
    for doc_type, fields in report.items():
        print(f"== {doc_type} ({len(fields)} discovered) ==")
        if not fields:
            print("   (none)\n")
            continue
        for f in fields:
            flag_str = ("  [" + ", ".join(f["flags"]) + "]") if f["flags"] else ""
            print(f"   - {f['name']} ({f['type']}, required={f['required']}){flag_str}")
            if f["validation_rule"]:
                print(f"     rule: {f['validation_rule']}")
        print()
    print(f"Total discovered: {total} | flagged suspicious: {flagged}")
    if total:
        print("Curate in: api/app/data/knowledge_base/field_catalog/<type>_fields.json")


if __name__ == "__main__":
    main()
