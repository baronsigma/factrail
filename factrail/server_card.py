"""Static MCP server card for FACTRAIL (served at /.well-known/mcp/server-card.json).

Shape: a superset combining
- Smithery's static server card (``serverInfo``, ``authentication``, ``tools``
  with ``inputSchema``, ``resources``, ``prompts``), which Smithery reads from
  ``/.well-known/mcp/server-card.json`` when scanning a URL-published server;
- the MCP Server Card extension (SEP-2127) identity/transport fields
  (``name``, ``version``, ``description``, ``title``, ``websiteUrl``,
  ``repository``, ``remotes`` with ``supportedProtocolVersions``).

``$schema`` is intentionally omitted: the SEP-2127 v1 schema caps
``description`` at 100 characters and excludes primitives such as ``tools``,
so this combined document would not validate against it.
"""

from __future__ import annotations

from typing import Any

from . import __version__
from .tool_catalog import listed_tools

SERVER_NAME = "io.github.baronsigma/factrail"
TITLE = "FACTRAIL"
DESCRIPTION = (
    "FACTRAIL — Evidence infrastructure for AI agents. Structured, source-backed real-world evidence "
    "with provenance, freshness, explicit uncertainty, and reusable receipts. French company "
    "verification is available now; trade/import assessment is in beta."
)
HOMEPAGE = "https://factrail.online/"
ENDPOINT = "https://mcp.factrail.online/mcp"
REPOSITORY = "https://github.com/baronsigma/factrail"


def _supported_protocol_versions() -> list[str]:
    """Protocol versions the installed MCP SDK negotiates, newest first."""
    try:
        from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS, MODERN_PROTOCOL_VERSIONS
        versions = set(MODERN_PROTOCOL_VERSIONS) | set(HANDSHAKE_PROTOCOL_VERSIONS)
    except Exception:  # older/newer SDK layouts
        versions = {"2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26"}
    return sorted(versions, reverse=True)


def _tool_entry(tool: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": tool.name,
        "title": tool.title,
        "description": tool.description,
        "inputSchema": tool.input_schema,
    }
    if tool.annotations is not None:
        entry["annotations"] = tool.annotations.model_dump(by_alias=True, exclude_none=True)
    return entry


def build_server_card() -> dict[str, Any]:
    return {
        "name": SERVER_NAME,
        "title": TITLE,
        "version": __version__,
        "description": DESCRIPTION,
        "homepage": HOMEPAGE,
        "websiteUrl": HOMEPAGE,
        "repository": {"url": REPOSITORY, "source": "github"},
        "endpoint": ENDPOINT,
        "transport": {"type": "streamable-http", "url": ENDPOINT},
        "remotes": [{
            "type": "streamable-http",
            "url": ENDPOINT,
            "supportedProtocolVersions": _supported_protocol_versions(),
        }],
        "serverInfo": {"name": "factrail", "title": TITLE, "version": __version__},
        "authentication": {"required": False, "schemes": []},
        "tools": [_tool_entry(tool) for tool in listed_tools()],
        "resources": [],
        "prompts": [],
    }
