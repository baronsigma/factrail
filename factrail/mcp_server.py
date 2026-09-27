"""MCP server exposing the verify_french_company tool."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import mcp.types as types
from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

from .models import FrenchCompany
from .sources.insee import (
    InseeAdapter,
    NotFoundError,
    UpstreamError,
    ValidationError,
)

logger = logging.getLogger(__name__)


async def handle_list_tools(
    context: Any, params: types.PaginatedRequestParams | None
) -> types.ListToolsResult:
    from .mcp_http_server import handle_list_tools as http_list_tools
    other_tools = [tool for tool in (await http_list_tools(context, params)).tools
                   if tool.name != "verify_french_company"]
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="verify_french_company",
                description="Verify a French company by SIREN or SIRET. "
                "Returns structured verified data from INSEE Sirene with "
                "BODACC company-event intelligence.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "identifier": {
                            "type": "string",
                            "description": "A 9-digit SIREN or 14-digit SIRET.",
                        }
                    },
                    "required": ["identifier"],
                },
            ),
            *other_tools,
        ]
    )


async def handle_call_tool(
    context: Any, params: types.CallToolRequestParams
) -> types.CallToolResult:
    if params.name in ("factrail_verify", "factrail_assess", "factrail_get_receipt", "assess_import", "analyze_company"):
        from .mcp_http_server import handle_call_tool as http_handle_call_tool
        return await http_handle_call_tool(context, params)
    if params.name != "verify_french_company":
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {params.name}"}))]
        )

    arguments = params.arguments or {}
    identifier = arguments.get("identifier", "")
    if not identifier:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps({"error": "identifier is required"}))]
        )

    adapter = InseeAdapter()
    try:
        result: FrenchCompany = adapter.lookup_with_bodacc(identifier)
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False),
                )
            ]
        )
    except ValidationError as exc:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps({"error": "validation", "message": str(exc)}))]
        )
    except NotFoundError as exc:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps({"error": "not_found", "message": str(exc)}))]
        )
    except UpstreamError as exc:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps({"error": "upstream", "message": str(exc)}))]
        )
    except Exception as exc:
        logger.exception("Unexpected error in verify_french_company")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps({"error": "internal", "message": str(exc)}))]
        )


app = Server(
    "factrail",
    version="2.1.0",
    on_list_tools=handle_list_tools,
    on_call_tool=handle_call_tool,
)


async def run() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="factrail",
                server_version="2.1.0",
                capabilities=app.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
