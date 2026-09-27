"""Global INSEE rate limiter with Retry-After support."""

from __future__ import annotations

import logging
import threading
import time as _time
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)

INSEE_QUOTA_LIMIT = 30
INSEE_QUOTA_WINDOW = 60
HEADROOM = 2


class RateLimiterError(Exception):
    pass


class InseeRateLimiter:
    def __init__(
        self,
        window_seconds: int = INSEE_QUOTA_WINDOW,
        max_requests: int = INSEE_QUOTA_LIMIT - HEADROOM,
        max_retry_wait: float = 15.0,
    ) -> None:
        self.window_seconds = window_seconds
        self.max_requests = max_requests
        self.max_retry_wait = max_retry_wait
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._timestamps and self._timestamps[0] <= cutoff:
            self._timestamps.popleft()

    def _wait_time(self, now: float) -> float:
        self._prune(now)
        if len(self._timestamps) < self.max_requests:
            return 0.0
        oldest = self._timestamps[0]
        wait = oldest + self.window_seconds - now
        return max(0.0, wait)

    def acquire(self, timeout: float = 60.0) -> float:
        deadline = _time.monotonic() + timeout
        with self._cond:
            while True:
                now = _time.monotonic()
                wait = self._wait_time(now)
                if wait == 0.0:
                    self._timestamps.append(now)
                    return 0.0
                remaining = deadline - now
                if wait > remaining:
                    raise RateLimiterError(
                        f"Rate limit requires {wait:.1f}s wait, "
                        f"only {remaining:.1f}s remaining"
                    )
                self._cond.wait(timeout=min(wait, 2.0))

    def handle_retry_after(self, retry_after: Optional[str]) -> float:
        if retry_after is None:
            return 0.0
        try:
            wait = float(retry_after)
        except (ValueError, TypeError):
            return 0.0
        wait = min(wait, self.max_retry_wait)
        _time.sleep(wait)
        return wait

    @property
    def current_count(self) -> int:
        with self._lock:
            self._prune(_time.monotonic())
            return len(self._timestamps)

    def stats(self) -> dict:
        return {
            "window_seconds": self.window_seconds,
            "max_requests": self.max_requests,
            "current_count": self.current_count,
            "utilization_pct": round(self.current_count / self.max_requests * 100, 1),
        }


_global_limiter = InseeRateLimiter()


def get_rate_limiter() -> InseeRateLimiter:
    return _global_limiter
