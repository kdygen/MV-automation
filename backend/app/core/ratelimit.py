"""Per-IP rate limiting for the public (unauthenticated) endpoints.

A simple in-memory sliding-window limiter: for each ``(endpoint, ip)`` key we keep the
timestamps of recent requests and refuse the call with **429** once the window is full.
State lives on ``app.state`` (one set of limiters per application instance), which is
correct for a single-process deployment (Render free tier) and keeps tests isolated —
each test builds a fresh app. When the platform scales to multiple replicas this moves
to a shared store (e.g. Redis); the interface stays the same.

The client IP comes from ``X-Forwarded-For`` (set by Render's proxy) with the direct
socket address as fallback. A spoofed header only lets an attacker throttle *itself*
into a different bucket — acceptable for abuse damping, not treated as identity.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

from fastapi import Depends, Request

from app.core.config import Settings, get_settings
from app.core.errors import RateLimitError


class SlidingWindowLimiter:
    """Allows at most ``max_requests`` per ``window_seconds`` per key."""

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        """Record a hit for ``key``; return False when the window is already full."""
        now = time.monotonic() if now is None else now
        cutoff = now - self.window_seconds
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.max_requests:
                return False
            hits.append(now)
            return True


def client_ip(request: Request) -> str:
    """Best-effort client address: first X-Forwarded-For hop, else the socket peer."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limited(
    name: str, *, max_requests: int, window_seconds: float
) -> Callable[..., None]:
    """Build a FastAPI dependency enforcing a per-IP limit for one endpoint group.

    Disabled when ``settings.rate_limit_enabled`` is false (e.g. targeted tests).
    """

    def dependency(
        request: Request, settings: Settings = Depends(get_settings)
    ) -> None:
        if not settings.rate_limit_enabled:
            return
        state = request.app.state
        if not hasattr(state, "rate_limiters"):
            state.rate_limiters = {}
        limiters: dict[str, SlidingWindowLimiter] = state.rate_limiters
        limiter = limiters.setdefault(
            name, SlidingWindowLimiter(max_requests, window_seconds)
        )
        if not limiter.allow(f"{name}:{client_ip(request)}"):
            raise RateLimitError(
                "Too many requests — please wait a few minutes and try again"
            )

    return dependency
