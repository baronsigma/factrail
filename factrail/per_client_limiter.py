"""Per-client rate limiting for the public FACTRAIL MCP endpoint.

Production runs behind a cloudflared tunnel, so the direct TCP peer is always
loopback.  The real client is therefore derived from ``CF-Connecting-IP``
(then the first ``X-Forwarded-For`` entry), but only when the direct peer is
loopback, i.e. a trusted local proxy.  Requests from known MCP gateways (for
example the Smithery gateway) are placed in a separate, larger bucket so that
one busy gateway does not starve, or get starved by, direct users.

The limiter never sleeps: excess requests are rejected immediately with
structured retry information (HTTP 429 + ``Retry-After`` in the middleware).

Environment variables (all optional):

``FACTRAIL_RATE_LIMIT_PER_MIN``          requests per window per client (default 60; 0 disables)
``FACTRAIL_RATE_WINDOW_SECONDS``         window length in seconds (default 60)
``FACTRAIL_RATE_BLOCK_AFTER``            rejected requests within one window that trigger a block (default 30; 0 disables)
``FACTRAIL_RATE_BLOCK_SECONDS``          block duration in seconds (default 300)
``FACTRAIL_GATEWAY_RATE_LIMIT_PER_MIN``  requests per window per gateway bucket (default 600; 0 disables)
``FACTRAIL_GATEWAY_RATE_BLOCK_AFTER``    block threshold for gateway buckets (default 0 = never block)
``FACTRAIL_GATEWAY_USER_AGENTS``         comma-separated case-insensitive User-Agent substrings identifying gateways (default ``SmitheryBot``)
``FACTRAIL_GATEWAY_CLIENT_HEADER``       optional header a gateway uses to forward an end-user id/IP; when set and present, gateway traffic is keyed per end user
"""

from __future__ import annotations

import ipaddress
import logging
import os
import threading
import time as _time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

DEFAULT_RATE_LIMIT_PER_MIN = 60
DEFAULT_RATE_WINDOW_SECONDS = 60
DEFAULT_RATE_BLOCK_AFTER = 30
DEFAULT_RATE_BLOCK_SECONDS = 300
DEFAULT_GATEWAY_RATE_LIMIT_PER_MIN = 600
DEFAULT_GATEWAY_RATE_BLOCK_AFTER = 0
DEFAULT_GATEWAY_USER_AGENTS = "SmitheryBot"

_MAX_KEY_LEN = 128


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Ignoring non-integer %s", name)
        return default
    return max(minimum, value)


class PerClientRateLimiter:
    """Sliding-window rate limiter keyed by client identifier.

    ``requests_per_window <= 0`` disables limiting.  Rejected requests are
    counted per window; after ``block_excessive_after`` rejections within one
    window the client is blocked for ``block_duration`` seconds
    (``block_excessive_after <= 0`` disables blocking).  ``max_wait`` is
    accepted for backwards compatibility and ignored: the limiter never blocks
    the caller.
    """

    def __init__(
        self,
        requests_per_window: int = DEFAULT_RATE_LIMIT_PER_MIN,
        window_seconds: int = DEFAULT_RATE_WINDOW_SECONDS,
        max_wait: float = 0.0,
        block_excessive_after: int = DEFAULT_RATE_BLOCK_AFTER,
        block_duration: int = DEFAULT_RATE_BLOCK_SECONDS,
    ) -> None:
        self.requests_per_window = requests_per_window
        self.window_seconds = max(1, int(window_seconds))
        self.max_wait = max_wait  # deprecated, unused
        self.block_excessive_after = block_excessive_after
        self.block_duration = block_duration
        self._windows: dict[str, list[float]] = defaultdict(list)
        self._rejections: dict[str, list[float]] = defaultdict(list)
        self._block_until: dict[str, float] = {}
        self._lock = threading.Lock()
        self._last_cleanup = 0.0

    @property
    def enabled(self) -> bool:
        return self.requests_per_window > 0

    def _result(self, allowed: bool, retry_after: float, remaining: int = 0) -> dict:
        return {
            "allowed": allowed,
            "retry_after": round(max(0.0, retry_after), 1),
            "limit": self.requests_per_window,
            "window": self.window_seconds,
            "remaining": max(0, remaining),
        }

    def _cleanup(self, now: float) -> None:
        cutoff = now - self.window_seconds
        for store in (self._windows, self._rejections):
            for cid in list(store.keys()):
                kept = [t for t in store[cid] if t > cutoff]
                if kept:
                    store[cid] = kept
                else:
                    del store[cid]
        for cid in list(self._block_until.keys()):
            if self._block_until[cid] <= now:
                del self._block_until[cid]

    def check(self, client_id: str) -> dict:
        """Check and record a request without blocking.

        Result shape::
            {"allowed": bool, "retry_after": float, "limit": int, "window": int, "remaining": int}
        """
        if not self.enabled:
            return self._result(True, 0.0, 0)
        now = _time.time()
        with self._lock:
            if now - self._last_cleanup >= 1.0:
                self._cleanup(now)
                self._last_cleanup = now

            blocked_until = self._block_until.get(client_id)
            if blocked_until is not None:
                if blocked_until > now:
                    return self._result(False, blocked_until - now)
                del self._block_until[client_id]

            cutoff = now - self.window_seconds
            window = [t for t in self._windows.get(client_id, ()) if t > cutoff]
            if len(window) < self.requests_per_window:
                window.append(now)
                self._windows[client_id] = window
                return self._result(True, 0.0, self.requests_per_window - len(window))
            self._windows[client_id] = window

            retry_after = window[0] + self.window_seconds - now
            rejections = [t for t in self._rejections.get(client_id, ()) if t > cutoff]
            rejections.append(now)
            self._rejections[client_id] = rejections
            if 0 < self.block_excessive_after <= len(rejections):
                self._block_until[client_id] = now + self.block_duration
                self._rejections.pop(client_id, None)
                retry_after = max(retry_after, float(self.block_duration))
                logger.warning("Client rate limit block for %ds (rejected: %d)",
                               self.block_duration, len(rejections))
            return self._result(False, retry_after)

    def reset(self, client_id: Optional[str] = None) -> None:
        with self._lock:
            if client_id is None:
                self._windows.clear()
                self._rejections.clear()
                self._block_until.clear()
            else:
                self._windows.pop(client_id, None)
                self._rejections.pop(client_id, None)
                self._block_until.pop(client_id, None)


# ---------------------------------------------------------------------------
# Client identification
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClientIdentity:
    key: str          # rate-limit bucket key
    kind: str         # "direct" or "gateway"
    gateway: Optional[str] = None


def _is_loopback(host: Optional[str]) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return host == "localhost"


def _clean_ip(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    candidate = value.strip().strip('"')
    if candidate.startswith("[") and "]" in candidate:
        candidate = candidate[1:candidate.index("]")]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        # IPv4 with port, e.g. "1.2.3.4:5678"
        if candidate.count(":") == 1:
            try:
                return str(ipaddress.ip_address(candidate.split(":")[0]))
            except ValueError:
                return None
        return None


def gateway_user_agents() -> list[str]:
    raw = os.environ.get("FACTRAIL_GATEWAY_USER_AGENTS")
    if raw is None:
        raw = DEFAULT_GATEWAY_USER_AGENTS
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def detect_gateway(headers: Mapping[str, str]) -> Optional[str]:
    """Return a gateway label when the request comes from a known MCP gateway.

    Smithery documents that its gateway sends ``User-Agent: SmitheryBot/1.0
    (+https://smithery.ai)`` from Cloudflare Workers; Cloudflare also stamps
    Worker subrequests with a ``CF-Worker`` header naming the Worker's zone,
    which is used as a secondary signal for Smithery.
    """
    ua = (headers.get("user-agent") or "").lower()
    for pattern in gateway_user_agents():
        if pattern in ua:
            return "smithery" if "smithery" in pattern else pattern.replace("/", "_")[:32]
    cf_worker = (headers.get("cf-worker") or "").lower()
    if "smithery" in cf_worker:
        return "smithery"
    return None


def resolve_client(peer_host: Optional[str], headers: Mapping[str, str]) -> ClientIdentity:
    """Derive the rate-limit identity of a request.

    Forwarding headers are trusted only when the direct peer is loopback (the
    local cloudflared tunnel).  Otherwise the TCP peer address is used.
    """
    client_ip: Optional[str] = None
    if _is_loopback(peer_host):
        client_ip = _clean_ip(headers.get("cf-connecting-ip"))
        if client_ip is None:
            xff = headers.get("x-forwarded-for") or ""
            client_ip = _clean_ip(xff.split(",")[0]) if xff else None
    if client_ip is None:
        client_ip = peer_host or "unknown"

    gateway = detect_gateway(headers)
    if gateway is not None:
        header_name = os.environ.get("FACTRAIL_GATEWAY_CLIENT_HEADER", "").strip().lower()
        end_user = headers.get(header_name, "").strip() if header_name else ""
        if end_user:
            return ClientIdentity(f"gateway:{gateway}:{end_user[:_MAX_KEY_LEN]}", "gateway", gateway)
        return ClientIdentity(f"gateway:{gateway}", "gateway", gateway)
    return ClientIdentity(f"ip:{client_ip[:_MAX_KEY_LEN]}", "direct")


# ---------------------------------------------------------------------------
# Singletons built from the environment
# ---------------------------------------------------------------------------

_global_limiter: Optional[PerClientRateLimiter] = None
_gateway_limiter: Optional[PerClientRateLimiter] = None
_global_limiter_lock = threading.Lock()


def limiter_from_env() -> PerClientRateLimiter:
    return PerClientRateLimiter(
        requests_per_window=_env_int("FACTRAIL_RATE_LIMIT_PER_MIN", DEFAULT_RATE_LIMIT_PER_MIN),
        window_seconds=_env_int("FACTRAIL_RATE_WINDOW_SECONDS", DEFAULT_RATE_WINDOW_SECONDS, 1),
        block_excessive_after=_env_int("FACTRAIL_RATE_BLOCK_AFTER", DEFAULT_RATE_BLOCK_AFTER),
        block_duration=_env_int("FACTRAIL_RATE_BLOCK_SECONDS", DEFAULT_RATE_BLOCK_SECONDS),
    )


def gateway_limiter_from_env() -> PerClientRateLimiter:
    return PerClientRateLimiter(
        requests_per_window=_env_int("FACTRAIL_GATEWAY_RATE_LIMIT_PER_MIN", DEFAULT_GATEWAY_RATE_LIMIT_PER_MIN),
        window_seconds=_env_int("FACTRAIL_RATE_WINDOW_SECONDS", DEFAULT_RATE_WINDOW_SECONDS, 1),
        block_excessive_after=_env_int("FACTRAIL_GATEWAY_RATE_BLOCK_AFTER", DEFAULT_GATEWAY_RATE_BLOCK_AFTER),
        block_duration=_env_int("FACTRAIL_RATE_BLOCK_SECONDS", DEFAULT_RATE_BLOCK_SECONDS),
    )


def get_per_client_limiter(*_args: Any, **_kwargs: Any) -> PerClientRateLimiter:
    """Return the process-wide limiter for direct clients (configured from env)."""
    global _global_limiter
    if _global_limiter is None:
        with _global_limiter_lock:
            if _global_limiter is None:
                _global_limiter = limiter_from_env()
    return _global_limiter


def get_gateway_limiter() -> PerClientRateLimiter:
    """Return the process-wide limiter for known gateway traffic."""
    global _gateway_limiter
    if _gateway_limiter is None:
        with _global_limiter_lock:
            if _gateway_limiter is None:
                _gateway_limiter = gateway_limiter_from_env()
    return _gateway_limiter
