"""Factrail V1.3.4 — Privacy-preserving MCP usage telemetry middleware.

Intercepts POST /mcp requests, identifies real tools/call invocations,
and records anonymous, coarse telemetry after the response is sent.
Never stores raw IPs, raw User-Agent, tool arguments, auth headers,
cookies, API keys, or SIREN/SIRET values.

Telemetry is completely non-blocking: any failure in telemetry
processing silently swallows the error and never affects the MCP response.
"""
from __future__ import annotations

import contextvars
import json
import logging
import time as _time
from typing import Any, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .telemetry import (
    build_client_context,
    is_telemetry_enabled,
    record_tool_call,
)

logger = logging.getLogger(__name__)

# Request-scoped cache/upstream status — isolated per async context
# so concurrent requests cannot leak status between each other.
# default=None ensures each context starts with no shared mutable dict.
_last_cache_status: contextvars.ContextVar[Optional[dict[str, str]]] = contextvars.ContextVar(
    "last_cache_status", default=None
)


def _set_cache_status(
    cache: Optional[str] = None, upstream: Optional[str] = None
) -> None:
    """Set cache/upstream status for the current request context.

    Passing None for a key leaves the current value unchanged.
    Always creates a new dict to avoid mutating inherited mutable state.
    """
    current = _last_cache_status.get()
    # Always copy to avoid mutating an inherited dict from a parent context
    status = dict(current) if current else {}
    if cache is not None:
        status["cache"] = cache
    if upstream is not None:
        status["upstream"] = upstream
    _last_cache_status.set(status)


class TelemetryMiddleware(BaseHTTPMiddleware):
    """Record anonymous usage telemetry for real tools/call executions.

    Filters out: MCP initialization, tools/list, discovery/protocol traffic,
    /healthz, /readyz.  Only counts actual tools/call JSON-RPC requests.
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if not is_telemetry_enabled():
            return await call_next(request)

        # Only care about POST /mcp
        if request.url.path != "/mcp" or request.method != "POST":
            return await call_next(request)

        # Read body to check if it's a tools/call
        body = await request.body()
        try:
            data = json.loads(body) if body else {}
        except (json.JSONDecodeError, ValueError):
            data = {}

        method = data.get("method", "")
        is_tool_call = method == "tools/call"

        # Build client context (anonymous, coarse)
        client_ctx = build_client_context(request)

        # If not a tools/call, skip telemetry
        if not is_tool_call:
            return await call_next(request)

        # Execute the request
        start = _time.monotonic()
        response = await call_next(request)
        latency_ms = (_time.monotonic() - start) * 1000

        # Determine success from response
        success = response.status_code == 200

        # Try to extract error type from response body
        error_type: Optional[str] = None
        if not success:
            error_type = self._classify_error(response)

        # Get cache and upstream status from the instrumented lookup
        status_dict = _last_cache_status.get() or {}
        cache_status = status_dict.pop("cache", "miss")
        upstream_status = status_dict.pop("upstream", "unknown")

        # Fire telemetry (never raises)
        tool_name = self._extract_tool_name(data)
        if tool_name:
            record_tool_call(
                tool_name=tool_name,
                success=success,
                latency_ms=latency_ms,
                cache_status=cache_status,
                upstream_status=upstream_status,
                client_family=client_ctx["client_family"],
                daily_client_hash=client_ctx["daily_client_hash"],
            )

        return response

    @staticmethod
    def _extract_tool_name(data: dict) -> Optional[str]:
        """Extract tool name from JSON-RPC params without storing arguments."""
        params = data.get("params", {})
        name = params.get("name")
        return name if isinstance(name, str) else None

    @staticmethod
    def _classify_error(response: Response) -> str:
        """Classify error type from HTTP response."""
        if response.status_code == 429:
            return "rate_limited"
        if response.status_code == 403:
            return "forbidden"
        if response.status_code == 421:
            return "host_rejected"
        if response.status_code >= 500:
            return "server_error"
        if response.status_code >= 400:
            return "client_error"
        return "unknown"
