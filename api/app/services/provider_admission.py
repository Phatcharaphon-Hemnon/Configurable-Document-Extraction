"""Endpoint-scoped provider admission states (cancellation safety).

Application task cancellation does NOT prove provider-side generation
stopped — there is no cancel API on OpenAI-compatible chat endpoints, so
a cancelled local task may leave the provider still generating. This
module tracks that uncertainty explicitly per effective endpoint:

- ``IDLE``: no reason to doubt the provider; dispatch allowed.
- ``UNKNOWN``: an application cancellation was confirmed while work may
  have been in flight. Automatic dispatch (queued auto-start, Resume,
  retries/requeues of other work) is refused with an explicit reason
  until an explicit verification event clears the state.

A cooldown timer alone never reopens dispatch, and there is no
operator-forced bypass: only a verification event clears the state.
Concretely: while UNKNOWN, every background-task start (uploads,
resumes, re-extracts) is refused at the run_job gate; the only exit is
:func:`mark_provider_verified`, recorded with reason and time.

State is process-local and shared across jobs for the same endpoint
(normalized URL). Cancellation itself is a request first (persisted
``cancellation_requested`` on the job), confirmed only after task cleanup.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

IDLE = "idle"
UNKNOWN = "unknown"


def _normalize_endpoint(endpoint: str | None) -> str:
    try:
        parsed = urlparse(endpoint or "")
    except Exception:
        return "default"
    host = (parsed.hostname or "").lower()
    if not host:
        return "default"
    return f"{parsed.scheme or 'https'}://{host}{parsed.path.rstrip('/')}"


_states: dict[str, dict] = {}


class ProviderBlocked(Exception):
    """Automatic dispatch refused: provider state is unknown after a
    cancellation. Verify the provider, then Resume/retry explicitly."""


def admission_state(endpoint: str | None) -> dict:
    """Current admission record (never raises; unknown endpoints are IDLE)."""
    return dict(_states.get(_normalize_endpoint(endpoint),
                           {"state": IDLE, "since": None, "reason": None}))


def automatic_dispatch_allowed(endpoint: str | None) -> tuple[bool, str]:
    """Whether an automatic path may dispatch to this endpoint now."""
    record = admission_state(endpoint)
    if record["state"] == UNKNOWN:
        return False, (
            "provider state unknown after cancellation"
            + (f" ({record['reason']})" if record.get("reason") else "")
            + "; verify the provider, then Resume or resubmit explicitly"
        )
    return True, "idle"


def require_automatic_dispatch(endpoint: str | None) -> None:
    """Raise ProviderBlocked unless automatic dispatch is allowed."""
    allowed, reason = automatic_dispatch_allowed(endpoint)
    if not allowed:
        raise ProviderBlocked(reason)


def note_cancel_confirmed(endpoint: str | None, reason: str) -> None:
    """Record post-cancellation doubt. Called only after task cleanup."""
    key = _normalize_endpoint(endpoint)
    _states[key] = {"state": UNKNOWN, "since": time.time(), "reason": reason}
    logger.warning("Provider admission %s -> unknown: %s", key, reason)


def mark_provider_verified(endpoint: str | None, reason: str) -> dict:
    """Clear doubt after an explicit verification event (evidence-based)."""
    key = _normalize_endpoint(endpoint)
    _states[key] = {"state": IDLE, "since": time.time(), "reason": reason}
    logger.info("Provider admission %s -> idle: %s", key, reason)
    return admission_state(endpoint)


def reset_admission(endpoint: str | None = None) -> None:
    """Forget admission state (tests; backend restart does this implicitly)."""
    if endpoint is None:
        _states.clear()
    else:
        _states.pop(_normalize_endpoint(endpoint), None)
