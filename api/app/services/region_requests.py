"""Opt-in region-request optimization (default off).

Builds on the measured compact A/B
(docs/reports/compact_request_ab_2026-09-16.md): both full-page conditions
timed out at 45s; compaction reduced server-counted prompt tokens but output
quality and speedup remain unverified, and condition B generated tokens
before cancellation. This module therefore optimizes REQUEST SHAPE only —
per-region serialized sizes are reported offline, never as a latency claim
(smaller individual requests do not necessarily reduce total runtime).

What it does (only when REGION_COMPACT_REQUEST=true AND the region path is
active; otherwise the legacy builder output is used byte-identically):

- Region-specific output contracts per splitter kind (header / table /
  totals / notes / page): prompt-level guidance reusing the shared
  extractor prompt downstream. The wire schema stays the full
  ExtractionResponseSchema — contracts NEVER restrict extraction to a
  subset of fields (in particular never to SROIE's scored fields); full
  catalog names remain allowed in every region.
- Coverage preservation: the splitter's coverage ledger is untouched —
  every source block stays assigned / excluded (blank only) / unresolved,
  unsupported or ambiguous boundaries stay explicit, no geometry is
  fabricated and no difficult content is omitted.
- Typed conversion of region candidates into the existing
  ExtractionCallResult contract (evidence, repeated columns, printed zeros,
  conflicts, and page isolation preserved) for merge_region_outputs.
- Finite sequential budgets: dispatch accounting derives from actual
  transport requests (the shared 4-attempt generation budget per region,
  page/job ceilings unchanged); output capacity and deadlines are never
  altered; when completing all regions cannot fit the existing job budget
  an explicit finding surfaces instead of silent skipping.
- Versioned fingerprints: REGION_REQUEST_VERSION enters region checkpoint
  fingerprints and the completed-result cache, so old entries resolve as
  misses instead of masquerading as new-mode results.

No live inference here: pure prompt-text construction, typed conversion,
budget arithmetic, and offline size accounting.
"""

from __future__ import annotations

import copy
import logging

logger = logging.getLogger(__name__)

# Bump whenever the contracts/builder/conversion logic below changes so
# checkpoint + result-cache fingerprints invalidate deterministically.
REGION_REQUEST_VERSION = "region-request-v1"

# Worst honest cost of ONE region call: the shared generation budget allows
# up to four HTTP attempts (initial + timeout retry + tier fallback +
# corrective generation). Estimates use this; accounting uses actuals.
WORST_ATTEMPTS_PER_REGION = 4

# Prompt-level output contracts per splitter region kind. Guidance only —
# the full catalog and the shared extractor rules still apply downstream,
# so any catalog field may appear in any region's output.
REGION_KIND_CONTRACTS: dict[str, str] = {
    "header": (
        "Region role: header / party details. Extract the visible header "
        "values (parties, identifiers, dates, labels) using catalog names "
        "verbatim — the FULL catalog remains allowed, not a fixed subset. "
        "Tables are expected to be empty here unless a small table is fully "
        "inside this region. Every value needs its own verbatim source_span "
        "quote from this region's text; omit absent fields, never invent."
    ),
    "table": (
        "Region role: item-row group. Return ALL data rows in order inside "
        "\"tables\" with their printed columns preserved (codes, discounts, "
        "units included); repeated columns and repeated values from distinct "
        "rows are kept, never deduped. Context lines marked as context are "
        "column headers, not data rows. Scalar fields visible in this region "
        "use catalog names verbatim — the FULL catalog remains allowed. "
        "Every cell needs its own verbatim source_span quote; null means "
        "unreadable, never invented."
    ),
    "totals": (
        "Region role: totals / amounts. Return the printed amount fields "
        "verbatim as printed (catalog names verbatim — the FULL catalog "
        "remains allowed, not a fixed subset). Never recalculate or rewrite "
        "amounts to balance; arithmetic mismatches are review findings, not "
        "edits. Every value needs its own verbatim source_span quote; omit "
        "absent fields, never invent."
    ),
    "notes": (
        "Region role: remaining notes / unmatched text. Extract the visible "
        "values using catalog names verbatim — the FULL catalog remains "
        "allowed, not a fixed subset; tables stay empty unless a table is "
        "fully inside this region. Every value needs its own verbatim "
        "source_span quote from this region's text; omit absent fields, "
        "never invent."
    ),
    "page": (
        "Region role: whole-page fallback. Extract ALL visible fields and "
        "tables using catalog names verbatim — the FULL catalog remains "
        "allowed. Every value needs its own verbatim source_span quote; "
        "omit absent fields, never invent."
    ),
}


def region_request_enabled(settings: object) -> bool:
    """Whether the region-request optimization is explicitly enabled.

    Strict `is True` so legacy MagicMock settings doubles (any attribute is
    a truthy Mock) keep the current default-off behavior.
    """
    return getattr(settings, "region_compact_request", False) is True


def build_region_request_text(region, *, enabled: bool = True) -> str:
    """Render one region as a focused extraction task with its kind contract.

    `enabled=False` returns the legacy builder output byte-identically (the
    default-off path). `enabled=True` prepends the kind-specific output
    contract and role context, then reuses build_region_task_text so region
    bodies, row boundaries, and context notes travel verbatim. No geometry
    is added or altered.
    """
    from app.services.regions import build_region_task_text

    legacy = build_region_task_text(region)
    if not enabled:
        return legacy
    contract = REGION_KIND_CONTRACTS.get(
        getattr(region, "kind", "page"), REGION_KIND_CONTRACTS["page"])
    header = (
        f"[Region-request {REGION_REQUEST_VERSION}: "
        f"{getattr(region, 'kind', 'page')} region — "
        "extract this region only; the full catalog and shared extractor "
        "rules still apply.]\n\n"
        f"[Output contract for this region: {contract}]"
    )
    return f"{header}\n\n{legacy}"


def convert_region_call(region, call, *, page_number: int) -> dict:
    """Convert one region LLM call into the existing typed merge input.

    Returns {"region", "fields", "tables"} for merge_region_outputs with:
    - page isolation: the call must belong to `page_number` (cross-page
      calls raise instead of leaking across pages);
    - deep-copied fields/tables so later mutation cannot alias the caller's
      objects (repeated columns, printed zeros, and evidence preserved
      verbatim — no filtering happens here);
    - region provenance stamped per field for merge conflict attribution.
    """
    if int(getattr(call, "page_number", page_number)) != int(page_number):
        raise ValueError(
            f"Region {getattr(region, 'id', '?')} call belongs to page "
            f"{getattr(call, 'page_number', '?')}, not page {page_number}")
    rid = getattr(region, "id", "?")
    fields = copy.deepcopy(list(getattr(call, "fields", []) or []))
    tables = copy.deepcopy(list(getattr(call, "tables", []) or []))
    for field in fields:
        try:
            field._region_id = rid  # type: ignore[attr-defined]
        except Exception:
            pass
    return {"region": region, "fields": fields, "tables": tables}


def estimate_region_dispatch_budget(
    *,
    n_regions: int,
    page_used: int,
    job_used: int,
    page_cap: int,
    job_cap: int,
    per_region_worst: int = WORST_ATTEMPTS_PER_REGION,
) -> dict:
    """Pre-flight worst-case budget check (advisory; caps never mutated).

    `needed_worst_case` = n_regions × shared 4-attempt generation budget.
    `fits` is False when the worst honest cost exceeds the remaining page
    or job ceiling; `message` names the binding existing cap so the
    shortfall surfaces explicitly instead of silently skipping regions.
    Actual accounting still derives from real transport dispatches.
    """
    needed = int(n_regions) * int(per_region_worst)
    remaining_page = int(page_cap) - int(page_used)
    remaining_job = int(job_cap) - int(job_used)
    fits = needed <= remaining_page and needed <= remaining_job
    message = ""
    if not fits:
        binding = (
            f"page ceiling {page_cap} (remaining {remaining_page})"
            if needed > remaining_page
            else f"job ceiling {job_cap} (remaining {remaining_job})"
        )
        message = (
            f"Region dispatch budget shortfall: completing all {n_regions} "
            f"region(s) may need up to {needed} dispatches (worst honest "
            f"cost {per_region_worst}/region) but the existing budget allows "
            f"less ({binding}). Output capacity and deadlines are unchanged; "
            "some regions may stay unresolved — see per-region findings."
        )
    return {
        "fits": fits,
        "needed_worst_case": needed,
        "remaining_page": remaining_page,
        "remaining_job": remaining_job,
        "message": message,
    }


def summarize_region_requests(regions, task_texts: dict[str, str]) -> dict:
    """Offline serialized-size report (content-free sizes only, no latency).

    Returns per-region request chars, the total, and duplicated-context
    chars (kind-contract overhead + repeated column-context notes shared
    across table groups). Smaller individual requests do NOT imply faster
    runs — this report carries no timing claim of any kind.
    """
    per_region = {r.id: len(task_texts.get(r.id, "")) for r in regions}
    overhead = sum(len(REGION_KIND_CONTRACTS.get(r.kind, "")) for r in regions)
    repeated_context = sum(len(r.context_note or "") for r in regions)
    return {
        "version": REGION_REQUEST_VERSION,
        "per_region_chars": per_region,
        "total_chars": sum(per_region.values()),
        "duplicated_context_chars": overhead + repeated_context,
        "n_regions": len(regions),
    }


def region_request_fingerprint_part(settings: object) -> dict:
    """Fingerprint fragment: version + opt-in flag (no credentials)."""
    return {
        "version": REGION_REQUEST_VERSION,
        "enabled": bool(region_request_enabled(settings)),
    }
