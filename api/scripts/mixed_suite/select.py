"""Deterministic per-dataset selection: sorted source IDs + recorded seed.

Never selects on model predictions or expected accuracy.
"""

from __future__ import annotations

import random


def select_ids(sorted_ids: list[str], count: int, seed: int) -> tuple[list[str], list[str]]:
    """Return (selected, skipped_duplicates_or_invalid).

    Deterministic Fisher-Yates partial draw over the pre-sorted ID list.
    """
    rng = random.Random(seed)
    pool = list(sorted_ids)
    selected: list[str] = []
    while pool and len(selected) < count:
        idx = rng.randrange(len(pool))
        candidate = pool.pop(idx)
        if candidate in selected:
            continue
        selected.append(candidate)
    return sorted(selected), []
