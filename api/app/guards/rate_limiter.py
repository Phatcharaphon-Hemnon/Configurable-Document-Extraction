"""Rate limiting guards for API requests."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting."""

    max_requests: int = 100
    window_seconds: int = 60
    max_concurrent: int = 5
    burst_limit: int = 10


@dataclass
class RateLimitState:
    """State tracking for rate limiting."""

    requests: list[float] = field(default_factory=list)
    concurrent: int = 0


class RateLimiter:
    """In-memory rate limiter for API requests.

    Tracks requests per client ID (typically IP address) and enforces
    window-based and concurrent request limits.

    Example:
        limiter = RateLimiter(RateLimitConfig(max_requests=10, window_seconds=60))
        allowed, reason = limiter.check_rate_limit("192.168.1.1")
        if allowed:
            limiter.record_request("192.168.1.1")
            try:
                # Process request
                ...
            finally:
                limiter.record_completion("192.168.1.1")
    """

    def __init__(self, config: RateLimitConfig | None = None):
        self.config = config or RateLimitConfig()
        self._states: dict[str, RateLimitState] = defaultdict(RateLimitState)

    def _cleanup_old_requests(self, state: RateLimitState) -> None:
        """Remove requests outside the current window."""
        cutoff = time.time() - self.config.window_seconds
        state.requests = [t for t in state.requests if t > cutoff]

    def check_rate_limit(self, client_id: str) -> tuple[bool, Optional[str]]:
        """Check if a request is allowed.

        Args:
            client_id: Identifier for the client (e.g., IP address).

        Returns:
            Tuple of (allowed, reason). If allowed is False, reason contains
            the rejection message.
        """
        state = self._states[client_id]
        self._cleanup_old_requests(state)

        # Check window-based limit
        if len(state.requests) >= self.config.max_requests:
            retry_after = self.get_retry_after(client_id)
            return (
                False,
                f"Rate limit exceeded: {self.config.max_requests} requests "
                f"per {self.config.window_seconds}s. Retry after {retry_after}s",
            )

        # Check concurrent limit
        if state.concurrent >= self.config.max_concurrent:
            return (
                False,
                f"Concurrent request limit exceeded: {self.config.max_concurrent}",
            )

        # Check burst limit (many requests in short time)
        recent_requests = [t for t in state.requests if t > time.time() - 5]
        if len(recent_requests) >= self.config.burst_limit:
            return (
                False,
                f"Burst limit exceeded: {self.config.burst_limit} requests in 5s",
            )

        return True, None

    def record_request(self, client_id: str) -> None:
        """Record a new request for the client."""
        state = self._states[client_id]
        state.requests.append(time.time())
        state.concurrent += 1
        logger.debug(
            "Request recorded for %s (total: %d, concurrent: %d)",
            client_id,
            len(state.requests),
            state.concurrent,
        )

    def record_completion(self, client_id: str) -> None:
        """Record request completion for the client."""
        state = self._states[client_id]
        state.concurrent = max(0, state.concurrent - 1)
        logger.debug(
            "Request completed for %s (concurrent: %d)",
            client_id,
            state.concurrent,
        )

    def get_retry_after(self, client_id: str) -> int:
        """Get seconds until next request is allowed.

        Args:
            client_id: Identifier for the client.

        Returns:
            Seconds to wait (0 if requests are allowed now).
        """
        state = self._states[client_id]
        self._cleanup_old_requests(state)

        if len(state.requests) < self.config.max_requests:
            return 0

        oldest_in_window = min(state.requests)
        wait = int(oldest_in_window + self.config.window_seconds - time.time()) + 1
        return max(0, wait)

    def get_client_stats(self, client_id: str) -> dict:
        """Get rate limit stats for a client."""
        state = self._states[client_id]
        self._cleanup_old_requests(state)
        return {
            "requests_in_window": len(state.requests),
            "concurrent": state.concurrent,
            "max_requests": self.config.max_requests,
            "window_seconds": self.config.window_seconds,
            "retry_after": self.get_retry_after(client_id),
        }

    def reset_client(self, client_id: str) -> None:
        """Reset rate limit state for a client."""
        if client_id in self._states:
            del self._states[client_id]
            logger.debug("Rate limit state reset for %s", client_id)
