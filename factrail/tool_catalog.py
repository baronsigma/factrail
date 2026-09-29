"""Single source of truth for FACTRAIL MCP tool metadata.

Used by the Streamable HTTP server (tools/list), the stdio server, and the
static server card served at ``/.well-known/mcp/server-card.json``.
"""

from __future__ import annotations

import os
from typing import Any

import mcp.types as types

from .evidence.company_fr import FIELD_MAP
from .evidence.models import EvidenceEnvelope
from .trade.models import AssessImportInput

LEGACY_TOOL_NAMES = frozenset({"verify_french_company", "assess_import", "analyze_company"})


def hide_legacy_tools() -> bool:
    """FACTRAIL_HIDE_LEGACY_TOOLS=1/true/yes hides legacy tools from tools/list (default off).

    Hidden legacy tools remain callable for backwards compatibility.
    """
    return os.environ.get("FACTRAIL_HIDE_LEGACY_TOOLS", "").strip().lower() in ("1", "true", "yes", "on")


def _annotations(title: str, *, open_world: bool) -> types.ToolAnnotations:
    return types.ToolAnnotations(
        title=title,
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=open_world,
    )


def _tool(name: str, title: str, description: str, input_schema: dict[str, Any], *,
          open_world: bool, output_schema: dict[str, Any] | None = None) -> types.Tool:
    return types.Tool(
        name=name,
        title=title,
        description=description,
        input_schema=input_schema,
        output_schema=output_schema,
        annotations=_annotations(title, open_world=open_world),
    )


ASSESS_IMPORT_LEGACY_SCHEMA: dict[str, Any] = {
    "type": "object",
        "properties": {
            "product": {
                "type": "string",
                "description": "Short product description (e.g. '750ml insulated stainless steel drinking bottle').",
            },
            "origin_country": {
                "type": "string",
                "description": "ISO 3166-1 alpha-2 origin country code (e.g. 'CN').",
            },
            "destination_country": {
                "type": "string",
                "description": "ISO 3166-1 alpha-2 EU destination country code (e.g. 'FR'). V0 supports EU member states only.",
            },
            "quantity": {
                "type": "integer",
                "description": "Number of units.",
            },
            "goods_value": {
                "type": "number",
                "description": "Total goods value in the given currency.",
            },
            "currency": {
                "type": "string",
                "description": "ISO 4217 currency code (e.g. 'EUR').",
            },
            "known_hs_code": {
                "type": "string",
                "description": "Optional known HS code (6-10 digits). If supplied, validated and enriched.",
            },
            "material": {
                "type": "string",
                "description": "Optional primary material (e.g. 'stainless steel').",
            },
            "weight_kg": {
                "type": "number",
                "description": "Optional gross weight per unit in kg.",
            },
            "dimensions": {
                "type": "string",
                "description": "Optional product dimensions (e.g. '25x8x8 cm').",
            },
            "manufacturer": {
                "type": "string",
                "description": "Optional manufacturer name.",
            },
            "model": {
                "type": "string",
                "description": "Optional model identifier.",
            },
            "incoterm": {
                "type": "string",
                "description": "Optional Incoterm (e.g. 'FCA', 'CIF', 'DDP').",
            },
            "shipping_mode": {
                "type": "string",
                "description": "Optional shipping mode (e.g. 'sea', 'air', 'road').",
            },
            "origin_location": {
                "type": "string",
                "description": "Optional origin city/port/location.",
            },
            "destination_location": {
                "type": "string",
                "description": "Optional destination city/port/location.",
            },
            "freight_cost": {
                "type": "number",
                "description": "Optional total freight cost in the given currency.",
            },
            "insurance_cost": {
                "type": "number",
                "description": "Optional total insurance cost in the given currency.",
            },
        },
        "required": [
            "product",
            "origin_country",
            "destination_country",
            "quantity",
            "goods_value",
            "currency",
        ],
    }


def all_tools() -> list[types.Tool]:
    """Every tool FACTRAIL can serve, including legacy compatibility tools."""
    envelope_schema = EvidenceEnvelope.model_json_schema()
    return [
        _tool(
            "factrail_capabilities",
            "FACTRAIL Capabilities",
            "Use when you need to discover what FACTRAIL can verify or assess before calling another tool. "
            "Returns available capabilities, inputs, fields, sources, versions, and limitations; call first "
            "to inspect current coverage and source state.",
            {"type": "object", "properties": {
                "operation": {"type": "string", "enum": ["verify", "assess"]},
                "capability": {"type": "string", "enum": ["company_fr", "import"]}}, "additionalProperties": False},
            output_schema={"type": "object", "properties": {"capabilities": {"type": "array", "items": {"type": "object"}}}, "required": ["capabilities"]},
            open_world=False,
        ),
        _tool(
            "factrail_assess",
            "Assess EU Import (Beta)",
            "Use when you need a source-backed assessment of importing a product into the EU (tariff "
            "classification, duties, and related evidence). Returns an EvidenceEnvelope; import assessment "
            "is Beta and results expose source coverage and support levels.",
            {"type": "object", "properties": {
                "assessment_type": {"type": "string", "enum": ["import"], "description": "Currently only import is supported."},
                "parameters": {**AssessImportInput.model_json_schema(), "additionalProperties": False}},
                "required": ["assessment_type", "parameters"], "additionalProperties": False},
            output_schema=envelope_schema,
            open_world=True,
        ),
        _tool(
            "factrail_verify",
            "Verify French Company",
            "Use when you need verified, source-backed facts about a French company from its SIREN or SIRET. "
            "Returns Evidence with provenance, freshness, coverage, conflicts, and a reusable receipt.",
            {"type": "object", "properties": {
                "subject_type": {"type": "string", "enum": ["company_fr"], "description": "Currently only French companies are supported."},
                "identifier": {"type": "string", "pattern": "^([0-9]{9}|[0-9]{14})$", "description": "A 9-digit SIREN or 14-digit SIRET."},
                "fields": {"type": "array", "minItems": 1, "items": {"type": "string", "enum": sorted(FIELD_MAP)}, "description": "Optional company fields to resolve; omit for the default set."}},
                "required": ["subject_type", "identifier"], "additionalProperties": False},
            output_schema=envelope_schema,
            open_world=True,
        ),
        _tool(
            "factrail_get_receipt",
            "Get Evidence Receipt",
            "Use when you need to re-fetch or audit a previously returned FACTRAIL result by its receipt ID. "
            "Recomputes the stored EvidenceEnvelope's content hash and rejects altered content; this checks "
            "integrity, not truth.",
            {"type": "object", "properties": {"receipt_id": {"type": "string", "pattern": "^fr_[0-9a-f]{64}$", "description": "A FACTRAIL evidence observation ID."}}, "required": ["receipt_id"], "additionalProperties": False},
            output_schema=envelope_schema,
            open_world=False,
        ),
        _tool(
            "verify_french_company",
            "[Deprecated] French Company Lookup",
            "Deprecated: use factrail_verify instead. Use when an older integration still expects the legacy "
            "flat French company lookup by SIREN/SIRET; kept callable for compatibility only.",
            {
                "type": "object",
                "properties": {
                    "identifier": {
                        "type": "string",
                        "description": "A 9-digit SIREN or 14-digit SIRET.",
                    }
                },
                "required": ["identifier"],
            },
            open_world=True,
        ),
        _tool(
            "assess_import",
            "[Deprecated] EU Import Assessment",
            "Deprecated: use factrail_assess instead. Use when an older integration still expects the legacy "
            "beta EU import assessment format; coverage may be partial or provisional and this is not a "
            "binding customs result.",
            ASSESS_IMPORT_LEGACY_SCHEMA,
            open_world=True,
        ),
        _tool(
            "analyze_company",
            "[Deprecated] Company Analysis",
            "Deprecated: use factrail_verify instead. Use only when an older integration still calls this "
            "experimental tool; financial and credit data sources are unavailable and returned fields are null.",
            {
                "type": "object",
                "properties": {
                    "siren": {
                        "type": "string",
                        "description": "Optional SIREN (9-digit) or SIRET (14-digit) identifier.",
                    },
                    "company_name": {
                        "type": "string",
                        "description": "Optional caller-provided display name; it is echoed without verification.",
                    },
                    "country_code": {
                        "type": "string",
                        "description": "ISO 3166-1 alpha-2 country code.",
                    },
                },
                "required": [],
            },
            open_world=True,
        ),
    ]


def listed_tools() -> list[types.Tool]:
    """Tools advertised in tools/list, honouring FACTRAIL_HIDE_LEGACY_TOOLS."""
    tools = all_tools()
    if hide_legacy_tools():
        tools = [tool for tool in tools if tool.name not in LEGACY_TOOL_NAMES]
    return tools
