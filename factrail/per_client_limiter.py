"""Per-client rate limiter to prevent anonymous callers from consuming all upstream capacity."""

from __future__ import annotations

import threading
import time as _time
from collections import defaultdict
from typing import Optional

import logging

logger = logging.getLogger(__name__)


class PerClientRateLimiter:
    """Sliding-window rate limiter keyed by client identifier (e.g., IP or token hash).

    Bounded quota waiting: returns a wait time or raises immediately if the
    caller must wait longer than ``max_wait``. Callers receive structured
    retry information rather than holding an MCP call for ~60 seconds.
    """

    def __init__(
        self,
        requests_per_window: int = 10,
        window_seconds: int = 60,
        max_wait: float = 2.0,
        block_excessive_after: int = 5,
        block_duration: int = 300,
    ) -> None:
        self.requests_per_window = requests_per_window
        self.window_seconds = window_seconds
        self.max_wait = max_wait
        self.block_excessive_after = block_excessive_after
        self.block_duration = block_duration
        self._windows: dict[str, list[float]] = defaultdict(list)
        self._block_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def _cleanup(self, now: float) -> None:
        cutoff = now - self.window_seconds
        for cid in list(self._windows.keys()):
            self._windows[cid] = [t for t in self._windows[cid] if t > cutoff]
            if not self._windows[cid]:
                del self._windows[cid]
        for cid in list(self._block_until.keys()):
            if self._block_until[cid] <= now:
                del self._block_until[cid]

    def check(self, client_id: str) -> dict:
        """Check and record a request. Returns a dict with allowed/retry info.

        Result shape::
            {"allowed": bool, "retry_after": float, "limit": int, "window": int}
        """
        now = _time.time()
        with self._lock:
            self._cleanup(now)

            # Check block list
            if client_id in self._block_until:
                remaining = self._block_until[client_id] - now
                return {
                    "allowed": False,
                    "retry_after": round(remaining, 1),
                    "limit": self.requests_per_window,
                    "window": self.window_seconds,
                }

            # Prune and count
            cutoff = now - self.window_seconds
            self._windows[client_id] = [
                t for t in self._windows[client_id] if t > cutoff
            ]
            current_count = len(self._windows[client_id])

            if current_count < self.requests_per_window:
                self._windows[client_id].append(now)
                return {
                    "allowed": True,
                    "retry_after": 0.0,
                    "limit": self.requests_per_window,
                    "window": self.window_seconds,
                }

            # Exceeded per-window quota
            oldest = self._windows[client_id][0]
            wait = oldest + self.window_seconds - now

            if wait <= self.max_wait:
                # Allow but throttle
                _time.sleep(wait)
                now = _time.time()
                self._windows[client_id] = [
                    t for t in self._windows[client_id] if t > now - self.window_seconds
                ]
                self._windows[client_id].append(now)
                return {
                    "allowed": True,
                    "retry_after": round(wait, 2),
                    "limit": self.requests_per_window,
                    "window": self.window_seconds,
                }

            # Block: too many requests — add to block list
            violations = current_count - self.requests_per_window
            if violations >= self.block_excessive_after:
                self._block_until[client_id] = now + self.block_duration
                logger.warning("Blocked client %s for %ds (excessive: %d)",
                               client_id[:8], self.block_duration, violations)

            return {
                "allowed": False,
                "retry_after": round(wait, 1),
                "limit": self.requests_per_window,
                "window": self.window_seconds,
            }

    def reset(self, client_id: Optional[str] = None) -> None:
        with self._lock:
            if client_id is None:
                self._windows.clear()
                self._block_until.clear()
            else:
                self._windows.pop(client_id, None)
                self._block_until.pop(client_id, None)


_global_limiter: Optional[PerClientRateLimiter] = None
_global_limiter_lock = threading.Lock()


def get_per_client_limiter(
    requests_per_window: int = 10,
    window_seconds: int = 60,
    max_wait: float = 2.0,
) -> PerClientRateLimiter:
    global _global_limiter
    if _global_limiter is None:
        with _global_limiter_lock:
            if _global_limiter is None:
                _global_limiter = PerClientRateLimiter(
                    requests_per_window=requests_per_window,
                    window_seconds=window_seconds,
                    max_wait=max_wait,
                )
    return _global_limiter
