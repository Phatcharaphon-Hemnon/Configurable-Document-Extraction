"""Staging-storage isolation: resolve symlinks and compare the staging dir
against every effective stateful path (KB, active gold, DB, sources, caches,
audit log). Being under ignored ``data-local/`` alone proves nothing.
"""

from __future__ import annotations

from pathlib import Path


def verify_staging_isolation(staging: Path, settings) -> dict[str, str]:
    """Resolve ``staging`` and assert no overlap with effective paths.

    Returns the resolved path map for the audit record. Raises ValueError on
    any overlap. ``settings`` is an ``app.core.config.Settings`` instance.
    """
    resolved = staging.resolve()
    effective = {
        "knowledge_base": Path(settings.knowledge_base_path).resolve(),
        "cache": Path(settings.cache_path).resolve(),
        "ocr_cache": Path(settings.ocr_cache_path).resolve(),
        "audit_log": Path(settings.audit_log_file).resolve(),
        "database": Path(settings.database_path).resolve(),
        "sources": Path(settings.source_storage_path).resolve(),
    }
    problems = []
    for name, other in effective.items():
        if resolved == other or other in resolved.parents or resolved in other.parents:
            problems.append(f"staging overlaps effective {name}: {other}")
    # Staging must live under data-local/ (ignored, evaluation-only).
    try:
        resolved.relative_to(Path(settings.cache_path).resolve().parents[0])
    except ValueError:
        problems.append(f"staging is not under the data-local tree: {resolved}")
    if "data-local" not in resolved.parts:
        problems.append(f"staging is not under an ignored data-local dir: {resolved}")
    if problems:
        raise ValueError("Staging isolation failed: " + "; ".join(problems))
    return {"staging": str(resolved), **{k: str(v) for k, v in effective.items()}}
