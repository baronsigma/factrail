"""Privacy-preserving context for one inbound MCP request."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
import os
import re
from typing import Any


@dataclass(frozen=True)
class RequestContext:
    request_id: str | None = None
    origin_class: str = "unknown"
    client_family: str = "unknown"
    mcp_client_name: str | None = None
    mcp_client_version: str | None = None
    server_version: str = "2.4.1"


_current: ContextVar[RequestContext] = ContextVar("factrail_request_context", default=RequestContext())
_VERSION = re.compile(r"^[A-Za-z0-9.+_-]{1,32}$")
_CLIENTS = {
    "claude desktop": "claude", "claude code": "claude", "claude": "claude",
    "chatgpt": "chatgpt", "cursor": "cursor", "visual studio code": "vscode",
    "vscode": "vscode", "codex": "codex", "mcp inspector": "mcp_inspector",
    "@modelcontextprotocol/inspector": "mcp_inspector",
}


def _clean_client_name(value: object) -> tuple[str | None, str]:
    if not isinstance(value, str):
        return None, "unknown"
    normalized = " ".join(value.strip().lower().split())
    family = _CLIENTS.get(normalized, "unknown")
    # Store a known normalized display value only, never arbitrary client strings.
    safe_name = normalized if family != "unknown" else None
    return safe_name, family


def from_mcp_context(context: Any) -> RequestContext:
    """Build context only from SDK-exposed request and initialize metadata."""
    request = getattr(context, "request", None)
    request_id = getattr(context, "request_id", None)
    request_id = str(request_id)[:64] if request_id is not None else None
    params = getattr(getattr(context, "session", None), "client_params", None)
    client_info = getattr(params, "client_info", None)
    name, family = _clean_client_name(getattr(client_info, "name", None))
    version = getattr(client_info, "version", None)
    version = version if isinstance(version, str) and _VERSION.fullmatch(version) else None
    headers = getattr(request, "headers", {}) if request is not None else {}
    marker = headers.get("x-factrail-test-origin") if headers else None
    if marker in {"internal_test", "live_test", "manual"}:
        origin = marker
    elif os.environ.get("FACTRAIL_TEST_MODE") == "1":
        origin = "internal_test"
    elif family == "mcp_inspector":
        origin = "inspector"
    elif family != "unknown":
        origin = "external"
    else:
        origin = "unknown"
    http_request_id = getattr(getattr(request, "state", None), "request_id", None)
    result = RequestContext(
        request_id=str(http_request_id or request_id)[:64] if (http_request_id or request_id) else None,
        origin_class=origin, client_family=family, mcp_client_name=name,
        mcp_client_version=version, server_version=os.environ.get("FACTRAIL_VERSION", "2.4.1"),
    )
    # The Starlette Request in MCP SDK metadata shares the ASGI scope state with
    # outer middleware. This is per-request state, not process-global state.
    if request is not None:
        try:
            request.state.factrail_request_context = result
        except Exception:
            pass
    return result


def current_request_context() -> RequestContext:
    return _current.get()


def bind_request_context(value: RequestContext):
    """Bind one request context and return a token for guaranteed reset."""
    return _current.set(value)


def reset_request_context(token: Any) -> None:
    _current.reset(token)
