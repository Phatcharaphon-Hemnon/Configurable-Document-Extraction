"""OCR-geometry region splitting, merging, and page reconciliation (opt-in).

Large extraction requests stall small local models, so an opt-in path
splits a page into focused tasks along ACTUAL OCR layout — never by blind
character counts, never with invented regions:

- ``header``: pre-table lines (party details stay together),
- ``table``: a detected header row + a bounded group of data rows,
- ``totals``: trailing amount-pattern lines,
- ``notes``: any other non-blank runs (never silently dropped),
- ``page``: whole-page fallback when geometry is missing or unreliable.

Every input block lands in exactly one coverage bucket: assigned to a
region, excluded with a reason (blank text only), or unresolved. Eight
rows is a GROUPING LIMIT, not a token budget — prompt sizes are recorded,
never assumed.

Row grouping uses more than Y-overlap: column-count consistency,
x-alignment of row starts, and vertical pitch. Ambiguous boundaries are
recorded as unresolved grouping notes for review, never guessed.
Deduplication removes only duplicated PROVENANCE (same name, value, AND
source span); equal values from distinct rows are preserved, with
conflicts retained for review. Totals arithmetic yields review findings
only — printed amounts are never rewritten to balance.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

SPLITTER_VERSION = "regions-v1"

# Grouping limit: at most this many data rows per table region. This bounds
# task shape only — it is NOT a prompt/token budget and callers must still
# record per-region prompt sizes.
MAX_TABLE_ROWS_PER_GROUP = 8

# Finite dispatch budgets for the opt-in region path (correction: the shared
# per-generation maximum of four HTTP attempts is preserved AND capped
# page/job-wide, so a many-region page can neither stall on four total
# calls nor fan out unboundedly):
# - worst honest single region = 4 attempts (shared generation budget);
# - page cap covers six full-budget regions with headroom;
# - job cap covers roughly two heavy pages or ten light ones.
MAX_REGION_DISPATCHES_PER_PAGE = 24
MAX_REGION_DISPATCHES_PER_JOB = 64


def build_region_task_text(region: Region) -> str:
    """Render one region as a focused, self-contained extraction task.

    The region text carries the data; context lines (e.g. repeated column
    headers) are marked as context, never data. Catalog and output contract
    come from the shared extractor prompt downstream — unchanged.
    """
    parts = [
        f"[Region {region.id} ({region.kind}), page {region.page_number} — "
        "extract ALL visible fields and tables in this region only.]",
    ]
    if region.context_note:
        parts.append(f"[Context, NOT data: {region.context_note}]")
    parts.append(region.text)
    parts.append(
        "[Evidence rule: quote source_span verbatim from the region text "
        "above; never invent values.]"
    )
    return "\n\n".join(part for part in parts if part)

_AMOUNT_RE = re.compile(r"\d[\d\s,]*[.,]\d{2}")
_TOTALS_KEYWORDS_RE = re.compile(
    r"\b(total|subtotal|sub-total|vat|tax|balance|amount due|amount-due|"
    r"grand total|net|discount|rounding|paid|change|tendered)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class VisualLine:
    """One visual text line: ordered blocks plus their union box."""

    texts: list[str]
    block_ids: list[str]
    boxes: list[tuple[float, float, float, float]]
    left: float = 0.0
    top: float = 0.0
    width: float = 0.0
    height: float = 0.0
    # The source block objects, in display order (member tracking so block
    # keys stay exact through continuation merges — never positional).
    members: list = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(self.texts)


@dataclass(slots=True)
class Region:
    """One focused extraction task over real OCR geometry."""

    id: str
    kind: str  # header | table | totals | notes | page
    page_number: int
    text: str
    block_ids: list[str]
    bbox: tuple[float, float, float, float] | None = None
    context_note: str | None = None
    split_reason: str | None = None


@dataclass(slots=True)
class CoverageLedger:
    """Every input block accounted for: assigned, excluded, or unresolved."""

    assigned: dict[str, str] = field(default_factory=dict)  # block_id -> region_id
    excluded: dict[str, str] = field(default_factory=dict)  # block_id -> reason
    unresolved: list[str] = field(default_factory=list)  # block_id, needs review


@dataclass(slots=True)
class SplitResult:
    regions: list[Region]
    coverage: CoverageLedger
    ambiguous_boundaries: list[str]
    fallback_reason: str | None = None


def _line_key(block) -> tuple[float, float]:
    box = block.box
    return (box[1] + box[3] / 2, box[0])


def group_lines(blocks: list) -> list[VisualLine]:
    """Group OCR blocks into visual lines (same rule family as layout_text).

    Returns lines top-to-bottom, blocks left-to-right, with union boxes.
    Blocks with degenerate boxes are skipped here and reported unresolved
    by the splitter.
    """
    usable = [b for b in (blocks or [])
              if getattr(b, "box", None) and b.box[2] > 0 and b.box[3] > 0]
    rows: list[list] = []
    for block in sorted(usable, key=_line_key):
        center = block.box[1] + block.box[3] / 2
        if rows and abs(center - (rows[-1][0].box[1] + rows[-1][0].box[3] / 2)) < max(
            5, block.box[3] * 0.6
        ):
            rows[-1].append(block)
        else:
            rows.append([block])
    lines: list[VisualLine] = []
    for row in rows:
        row = sorted(row, key=lambda b: b.box[0])
        left = min(b.box[0] for b in row)
        top = min(b.box[1] for b in row)
        right = max(b.box[0] + b.box[2] for b in row)
        bottom = max(b.box[1] + b.box[3] for b in row)
        lines.append(VisualLine(
            texts=[b.text for b in row],
            block_ids=[getattr(b, "block_id", None) or f"unid-{i}" for i, b in enumerate(row)],
            boxes=[tuple(b.box) for b in row],
            left=left, top=top, width=right - left, height=bottom - top,
            members=list(row),
        ))
    return lines


def _max_inner_gap(line: VisualLine) -> float:
    gaps = [line.boxes[i + 1][0] - (line.boxes[i][0] + line.boxes[i][2])
            for i in range(len(line.boxes) - 1)]
    return max(gaps) if gaps else 0.0


def _max_block_height(line: VisualLine) -> float:
    return max((box[3] for box in line.boxes), default=0.0)


def _is_table_line(line: VisualLine) -> bool:
    """A line shaped like a table row.

    Uses the same wide-gap evidence as the OCR layout layer (a gap wider
    than 1.5x the block height separates columns), or four-plus columns.
    """
    if len(line.texts) >= 4:
        return True
    if len(line.texts) >= 2 and _max_inner_gap(line) > _max_block_height(line) * 1.5:
        return True
    return False


def _is_totals_line(line: VisualLine) -> bool:
    text = line.text
    return bool(_TOTALS_KEYWORDS_RE.search(text) and _AMOUNT_RE.search(text))


def _same_logical_row(prev: VisualLine, current: VisualLine, typical_cols: int) -> bool:
    """Decide whether `current` continues `prev` as one multiline logical row.

    Y-overlap alone is NOT enough: require ALL of (a) tight vertical pitch,
    (b) x-start alignment within tolerance, (c) the previous line looking
    column-short (fewer blocks than the band's typical column count).
    Anything less is a new row; conflicting evidence is reported by the
    caller as an ambiguous boundary instead of being guessed.
    """
    if not prev.height or not current.height:
        return False
    gap = current.top - (prev.top + prev.height)
    pitch_ok = 0 <= gap <= max(prev.height, current.height) * 1.2
    if not pitch_ok:
        return False
    x_tol = max(prev.width, current.width) * 0.15 + 2.0
    aligned = abs(current.left - prev.left) <= x_tol
    short_prev = len(prev.texts) < max(2, typical_cols)
    return bool(aligned and short_prev)


def split_page(blocks: list, page_text: str | None, page_number: int = 1) -> SplitResult:
    """Split one OCR page into focused regions with a full coverage ledger."""
    blocks = list(blocks or [])
    ledger = CoverageLedger()
    ambiguous: list[str] = []

    def _block_key(block, index: int) -> str:
        return getattr(block, "block_id", None) or f"page-{page_number}-block-{index}"

    if not blocks or not (page_text or "").strip():
        reason = "no blocks" if not blocks else "blank page text"
        for index, block in enumerate(blocks):
            key = _block_key(block, index)
            if not (getattr(block, "text", "") or "").strip():
                ledger.excluded[key] = "blank"
            else:
                ledger.unresolved.append(key)
        return SplitResult(
            regions=[Region(
                id=f"page-{page_number}-region-page-0", kind="page",
                page_number=page_number, text=page_text or "",
                block_ids=[_block_key(b, i) for i, b in enumerate(blocks)
                           if (getattr(b, "text", "") or "").strip()],
                split_reason=f"full-page fallback: {reason}",
            )],
            coverage=ledger, ambiguous_boundaries=ambiguous,
            fallback_reason=reason,
        )

    # Degenerate geometry never enters layout reasoning.
    usable: list[tuple[int, object]] = []
    for index, block in enumerate(blocks):
        key = _block_key(block, index)
        if not (getattr(block, "text", "") or "").strip():
            ledger.excluded[key] = "blank"
            continue
        box = getattr(block, "box", None)
        if not box or box[2] <= 0 or box[3] <= 0:
            ledger.unresolved.append(key)
            continue
        usable.append((index, block))

    lines = group_lines([b for _, b in usable])
    if not lines:
        return SplitResult(
            regions=[Region(
                id=f"page-{page_number}-region-page-0", kind="page",
                page_number=page_number, text=page_text or "", block_ids=[],
                split_reason="full-page fallback: no usable lines",
            )],
            coverage=ledger, ambiguous_boundaries=ambiguous,
            fallback_reason="no usable lines",
        )

    # (Classification happens on merged lines below; raw lines exist only
    # to feed the continuation merge.)

    def _combine(first: VisualLine, second: VisualLine) -> VisualLine:
        left = min(first.left, second.left)
        top = min(first.top, second.top)
        right = max(first.left + first.width, second.left + second.width)
        bottom = max(first.top + first.height, second.top + second.height)
        return VisualLine(
            texts=first.texts + second.texts,
            block_ids=first.block_ids + second.block_ids,
            boxes=first.boxes + second.boxes,
            left=left, top=top, width=right - left, height=bottom - top,
            members=first.members + second.members,
        )

    # Continuation merge: fold a column-short, x-aligned, tight-pitch line
    # into an established (multi-block) preceding line. Conservative: the
    # previous line must already look like a row, so bare single-block
    # header lines never merge into each other.
    merged: list[VisualLine] = []
    page_typical = max((len(line.texts) for line in lines), default=2)
    for line in lines:
        if (merged and len(merged[-1].texts) >= 2
                and _same_logical_row(merged[-1], line, page_typical)):
            merged[-1] = _combine(merged[-1], line)
        else:
            merged.append(line)
    lines = merged
    kinds = ["table" if _is_table_line(line) else ("totals" if _is_totals_line(line) else "text")
             for line in lines]

    # Candidate bands: runs of ≥2 lines with ≥3 blocks each (repeated
    # multi-column rows count as table evidence even without wide gaps).
    for idx, line in enumerate(lines):
        if kinds[idx] == "text" and len(line.texts) >= 3:
            run = [idx]
            j = idx + 1
            while j < len(lines) and kinds[j] == "text" and len(lines[j].texts) >= 3:
                run.append(j)
                j += 1
            if len(run) >= 2:
                for k in run:
                    kinds[k] = "table"

    if not any(k == "table" for k in kinds):
        for index, _ in usable:
            ledger.assigned[_block_key(blocks[index], index)] = f"page-{page_number}-region-page-0"
        return SplitResult(
            regions=[Region(
                id=f"page-{page_number}-region-page-0", kind="page",
                page_number=page_number, text=page_text or "",
                block_ids=[k for k in ledger.assigned],
                split_reason="full-page fallback: no table structure detected",
            )],
            coverage=ledger, ambiguous_boundaries=ambiguous,
            fallback_reason="no table structure detected",
        )

    regions: list[Region] = []
    counter = 0

    # Stable keys per line straight from grouping members (never positional).
    key_by_id = {id(block): _block_key(blocks[index], index) for index, block in usable}
    line_keys: list[list[str]] = [[key_by_id[id(b)] for b in line.members] for line in lines]

    def _emit2(kind: str, line_idxs: list[int], context: str | None = None,
               reason: str | None = None) -> None:
        nonlocal counter
        if not line_idxs:
            return
        rid = f"page-{page_number}-region-{kind}-{counter}"
        counter += 1
        bids = [key for li in line_idxs for key in line_keys[li]]
        regions.append(Region(
            id=rid, kind=kind, page_number=page_number,
            text="\n".join(lines[li].text for li in line_idxs),
            block_ids=bids, context_note=context, split_reason=reason,
        ))
        for key in bids:
            ledger.assigned[key] = rid

    # Walk lines: header text, table bands (header + row groups), totals, notes.
    idx = 0
    n = len(lines)
    # Leading non-table lines form the header region (party details kept together).
    header_end = 0
    while header_end < n and kinds[header_end] != "table":
        header_end += 1
    if header_end > 0:
        _emit2("header", list(range(0, header_end)),
               reason="leading pre-table lines, positional (often party details)")
    while idx < n:
        if kinds[idx] == "table":
            band_start = idx
            while idx < n and kinds[idx] == "table":
                idx += 1
            band = list(range(band_start, idx))
            # Header row: nearest text line directly above the band with 2+
            # blocks; absent header is recorded, never invented.
            header_line: int | None = None
            if band_start > 0 and kinds[band_start - 1] == "text" and len(lines[band_start - 1].texts) >= 2:
                # Only claim it when it sits outside an already-emitted header region.
                if band_start - 1 >= header_end:
                    header_line = band_start - 1
            # In-band header: a digitless first band line followed by digit
            # lines is column context, not a data row (preferred over the
            # above-band claim when both exist).
            in_band_header: int | None = None
            if any(any(ch.isdigit() for ch in t) for li in band[1:]
                   for t in lines[li].texts):
                if not any(any(ch.isdigit() for ch in t) for t in lines[band[0]].texts):
                    in_band_header = band[0]
                    band = band[1:]
                    header_line = None
            typical_cols = max((len(lines[i].texts) for i in band), default=2)
            # Group data rows: ≤ MAX rows, keeping multiline logical rows
            # together; conflicting evidence -> ambiguous boundary note.
            groups: list[list[int]] = []
            current: list[int] = []
            for pos, li in enumerate(band):
                if current:
                    prev_line = lines[current[-1]]
                    if _same_logical_row(prev_line, lines[li], typical_cols):
                        current.append(li)
                        continue
                    # Boundary: check for conflicting evidence (tight pitch
                    # but misaligned starts, or aligned but column-full).
                    gap = lines[li].top - (prev_line.top + prev_line.height)
                    tight = 0 <= gap <= max(prev_line.height, lines[li].height) * 1.2
                    x_tol = max(prev_line.width, lines[li].width) * 0.15 + 2.0
                    aligned = abs(lines[li].left - prev_line.left) <= x_tol
                    if tight != aligned:
                        ambiguous.append(
                            f"page {page_number} lines {current[-1]}->{li}: "
                            f"tight-pitch={tight} x-aligned={aligned}; kept separate, needs review"
                        )
                if len(current) >= MAX_TABLE_ROWS_PER_GROUP:
                    groups.append(current)
                    current = []
                current.append(li)
            if current:
                # Enforce the grouping limit on the trailing group.
                while len(current) > MAX_TABLE_ROWS_PER_GROUP:
                    groups.append(current[:MAX_TABLE_ROWS_PER_GROUP])
                    current = current[MAX_TABLE_ROWS_PER_GROUP:]
                groups.append(current)
            for group in groups:
                context_lines: list[int] = []
                if header_line is not None:
                    context_lines.append(header_line)
                if in_band_header is not None:
                    context_lines.append(in_band_header)
                emit_lines = context_lines + group
                _emit2(
                    "table", emit_lines,
                    context=("first line(s) are repeated column context, not data"
                             if context_lines else None),
                    reason=f"table band lines {band[0]}-{band[-1]}, rows {len(group)} "
                           f"(grouping limit {MAX_TABLE_ROWS_PER_GROUP})",
                )
        elif kinds[idx] == "totals":
            run = [idx]
            idx += 1
            while idx < n and kinds[idx] == "totals":
                run.append(idx)
                idx += 1
            _emit2("totals", run, reason="trailing amount-pattern lines")
        else:
            run = [idx]
            idx += 1
            while idx < n and kinds[idx] == "text":
                run.append(idx)
                idx += 1
            # Skip lines already consumed as the pre-table header region.
            fresh = [li for li in run if li >= header_end or header_end == 0]
            if header_end > 0:
                fresh = [li for li in run if not (0 <= li < header_end)]
            _emit2("notes", fresh, reason="unmatched non-blank text kept as notes")

    # Safety net: any usable block still unaccounted becomes unresolved
    # (never silently dropped).
    accounted = set(ledger.assigned) | set(ledger.excluded)
    for index, _ in usable:
        key = _block_key(blocks[index], index)
        if key not in accounted:
            ledger.unresolved.append(key)
    return SplitResult(regions=regions, coverage=ledger,
                       ambiguous_boundaries=ambiguous, fallback_reason=None)


def fingerprint_region(
    *,
    job_id: str = "",
    source_id: str = "",
    page_number: int = 1,
    region: Region,
    block_texts: dict[str, str] | None = None,
    upstream_text_sha: str = "",
    catalog_sha: str = "",
    splitter_version: str = SPLITTER_VERSION,
    schema_version: str = "",
    prompt_version: str = "",
    model_fingerprint: dict | None = None,
) -> str:
    """Content fingerprint for checkpoint reuse (positional IDs insufficient).

    Covers composite identity (job/submission, source, page, region),
    geometry (block ids + boxes), source content (block texts + upstream
    text hash), catalog/examples hash, splitter/prompt/schema versions, and
    provider capabilities + generation settings. Any mismatch invalidates;
    there is no time-based expiry of validity (retention bounding is
    separate — see region store retention).
    """
    blocks_payload = [
        {"id": bid, "text": (block_texts or {}).get(bid, "")}
        for bid in region.block_ids
    ]
    core = {
        "v": 1,
        "splitter": splitter_version,
        "job": str(job_id),
        "source": str(source_id),
        "page": int(page_number),
        "region": region.id,
        "kind": region.kind,
        "region_text_sha": hashlib.sha256(region.text.encode("utf-8")).hexdigest(),
        "blocks": blocks_payload,
        "upstream_text_sha": upstream_text_sha,
        "catalog_sha": catalog_sha,
        "schema_version": schema_version,
        "prompt_version": prompt_version,
        "model": model_fingerprint or {},
    }
    return hashlib.sha256(
        json.dumps(core, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def merge_region_outputs(outputs: list[dict]) -> dict:
    """Deterministically merge per-region fields/tables.

    Each output: {"region": Region, "fields": [...], "tables": [...]} where
    fields/tables are ExtractedField/ExtractedTable (or model dumps).
    Rules: first occurrence wins per normalized field name; identical
    (name, value, source_span) triples from multiple regions collapse with
    a dedup note (duplicated provenance); same name with different value
    or span becomes a conflict retained for review (first kept). Table
    data rows are NEVER deduped (provenance tracked per row); only the
    repeated header-context lines are excluded from data by prompt contract.
    Returns {"fields", "tables", "conflicts", "row_provenance", "deduped"}.
    """
    from app.services.field_catalog import normalize_field_name  # local import: no cycle

    def _get(obj, name, default=None):
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)

    fields: list = []
    seen: dict[str, object] = {}
    conflicts: list[dict] = []
    deduped: list[dict] = []
    tables: list = []
    row_provenance: dict[str, list[str]] = {}
    for output in outputs:
        region = output.get("region")
        rid = getattr(region, "id", "?") if region is not None else "?"
        for f in output.get("fields", []) or []:
            name = normalize_field_name(str(_get(f, "name", "") or ""))
            if not name:
                continue
            value = _get(f, "value")
            span = _get(f, "source_span")
            if name in seen:
                prev = seen[name]
                if (value, span) == (_get(prev, "value"), _get(prev, "source_span")):
                    deduped.append({"name": name, "regions": [getattr(prev, "_region_id", "?"), rid]})
                else:
                    conflicts.append({
                        "name": name,
                        "kept": {"value": _get(prev, "value"), "region": getattr(prev, "region_id", "?")},
                        "dropped": {"value": value, "region": rid},
                    })
                continue
            try:
                f.region_id = rid  # type: ignore[attr-defined]
            except Exception:
                pass
            try:
                f._region_id = rid  # type: ignore[attr-defined]
            except Exception:
                pass
            seen[name] = f
            fields.append(f)
        for table in output.get("tables", []) or []:
            tname = _get(table, "name", "table")
            tables.append(table)
            rows = _get(table, "rows", []) or []
            row_provenance.setdefault(str(tname), []).extend([rid] * len(rows))
    return {"fields": fields, "tables": tables, "conflicts": conflicts,
            "row_provenance": row_provenance, "deduped": deduped}


def reconcile_page(
    *,
    doc_type: str,
    fields: list,
    tables: list,
    conflicts: list[dict],
    coverage: CoverageLedger,
    ambiguous_boundaries: list[str],
    tables_expected: bool,
) -> list:
    """Deterministic page-level reconciliation after region merging.

    Returns StructuredReviewIssue list. Unresolved blocks and ambiguous
    boundaries always flag the page incomplete. Conflicts flag review.
    Totals arithmetic yields findings only — printed amounts are NEVER
    rewritten. An empty finding list means reconciliation is clean (it does
    not by itself assert review-cleanliness; the validator still rules).
    """
    from app.schemas.documents import StructuredReviewIssue

    findings: list = []
    if coverage.unresolved:
        findings.append(StructuredReviewIssue(
            category="mechanical", target="page:unresolved-blocks",
            severity="error",
            evidence=f"{len(coverage.unresolved)} block(s) unassigned",
            explanation=(
                "OCR blocks could not be assigned to any region: "
                + ", ".join(coverage.unresolved[:8])
                + ("…" if len(coverage.unresolved) > 8 else "")
                + ". The page is incomplete; completed regions remain usable."
            ),
        ))
    for note in ambiguous_boundaries:
        findings.append(StructuredReviewIssue(
            category="row_column", target="page:row-grouping",
            severity="warning", evidence=None, explanation=note,
        ))
    for conflict in conflicts:
        findings.append(StructuredReviewIssue(
            category="unsupported", target=f"field:{conflict['name']}",
            severity="warning",
            evidence=None,
            explanation=(
                f"Same field from multiple regions with different values/evidence: "
                f"kept {conflict['kept']!r}, dropped {conflict['dropped']!r}. "
                "First occurrence retained; review before trusting."
            ),
        ))
    if tables_expected and not tables:
        findings.append(StructuredReviewIssue(
            category="row_column", target="page:tables",
            severity="warning", evidence=None,
            explanation="Table structure was detected for region splitting but no tables were produced.",
        ))

    def _num(value: object) -> float | None:
        try:
            if value is None or isinstance(value, bool):
                return None
            text = str(value).replace(",", "").strip()
            return float(text)
        except (ValueError, TypeError):
            return None

    amounts: dict[str, float] = {}
    for f in fields:
        lname = str(getattr(f, "name", "")).lower()
        number = _num(getattr(f, "value", None))
        if number is None:
            continue
        if lname in ("total_amount", "subtotal_amount", "tax_amount"):
            amounts[lname] = number
    if all(k in amounts for k in ("total_amount", "subtotal_amount", "tax_amount")):
        expected = amounts["subtotal_amount"] + amounts["tax_amount"]
        if abs(expected - amounts["total_amount"]) > 0.015:
            findings.append(StructuredReviewIssue(
                category="mechanical", target="field:total_amount",
                severity="warning",
                evidence=f"subtotal={amounts['subtotal_amount']} tax={amounts['tax_amount']} "
                         f"total={amounts['total_amount']}",
                explanation=(
                    "Printed amounts do not balance (subtotal + tax != total). "
                    "Values are kept verbatim as printed; review required."
                ),
            ))
    return findings
