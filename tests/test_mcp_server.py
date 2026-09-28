"""Tests for the MCP server handlers."""

from __future__ import annotations

import asyncio
import json

import pytest
import respx

from factrail.mcp_server import handle_list_tools, handle_call_tool
import mcp.types as types


class TestMcpServer:
    @pytest.fixture
    def ctx(self):
        return None

    @pytest.mark.asyncio
    async def test_list_tools(self, ctx):
        result = await handle_list_tools(ctx, None)
        assert len(result.tools) == 7
        assert result.tools[0].name == "verify_french_company"

    @respx.mock
    @pytest.mark.asyncio
    async def test_call_tool_siren(self, ctx):
        raw = {
            "header": {"statut": 200, "message": "OK"},
            "uniteLegale": {
                "siren": "784671695",
                "dateCreationUniteLegale": "1990-01-01",
                "trancheEffectifsUniteLegale": "01",
                "anneeEffectifsUniteLegale": "2023",
                "categorieJuridiqueUniteLegale": "9220",
                "periodesUniteLegale": [
                    {
                        "dateFin": None,
                        "dateDebut": "2020-01-01",
                        "etatAdministratifUniteLegale": "A",
                        "denominationUniteLegale": "UNICEF FRANCE",
                        "activitePrincipaleUniteLegale": "94.99Z",
                        "nomenclatureActivitePrincipaleUniteLegale": "NAFRev2",
                    }
                ],
            },
        }
        respx.get("https://api.insee.fr/api-sirene/3.11/siren/784671695").mock(
            return_value=__import__("httpx").Response(200, json=raw)
        )

        params = types.CallToolRequestParams(
            name="verify_french_company",
            arguments={"identifier": "784671695"},
        )
        result = await handle_call_tool(ctx, params)
        text = result.content[0].text
        data = json.loads(text)
        assert data["siren"] == "784671695"
        assert data["legal_name"] == "UNICEF FRANCE"
        assert data["naf_code_nomenclature"] == "NAFRev2"
        assert data.get("trade_name_reason") == "unavailable"

    @pytest.mark.asyncio
    async def test_call_tool_validation_error(self, ctx):
        params = types.CallToolRequestParams(
            name="verify_french_company",
            arguments={"identifier": "123"},
        )
        result = await handle_call_tool(ctx, params)
        data = json.loads(result.content[0].text)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_call_tool_missing_identifier(self, ctx):
        params = types.CallToolRequestParams(
            name="verify_french_company",
            arguments={},
        )
        result = await handle_call_tool(ctx, params)
        data = json.loads(result.content[0].text)
        assert "error" in data
