"""FACTRAIL v2.4.0 public beta remote MCP server (Streamable HTTP).

Aligned with MCP specification 2026-07-28:
- POST /mcp: stateless JSON-RPC requests (no protocol sessions, no GET streams)
- MCP-Protocol-Version header validated by SDK transport
- Origin header validation (DNS rebinding protection)
- Per-client rate limiting with bounded quota waiting
- SQLite cache with TTL, schema versioning, stale-on-upstream-failure
- Structured logs: request ID, latency, cache state, upstream sources
- /healthz and /readyz endpoints
- Graceful shutdown via SIGINT/SIGTERM
- Tool annotations: read-only, non-destructive, open-world data

Backward compatibility: supports older protocol versions (2025-03-26, 2025-11-25)
via SDK-negotiated fallback.

Core MCP tools: factrail_capabilities, factrail_verify, factrail_assess,
factrail_get_receipt. Domain-specific tools remain for compatibility.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time as _time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Optional

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp

import mcp.types as types
from mcp.server import Server
from mcp.server.transport_security import TransportSecuritySettings

from .cache import get_cache
from .evidence.models import EvidenceEnvelope
from .evidence.company_fr import FIELD_MAP
from .evidence.receipts import RECEIPT_ID_PATTERN
from .models import FrenchCompany
from .trade.models import AssessImportInput
from .per_client_limiter import get_per_client_limiter
from .sources.insee import (
    InseeAdapter,
    NotFoundError,
    UpstreamError,
    ValidationError,
)
from .telemetry import is_telemetry_enabled, build_client_context, record_tool_call
from .telemetry_middleware import TelemetryMiddleware, _set_cache_status

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structured logging middleware
# ---------------------------------------------------------------------------

class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    """Inject request ID, log method/path/latency/cache_state, never log secrets."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        request_id = uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        start = _time.monotonic()

        logger.info(
            "mcp_inbound",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
            },
        )

        response = await call_next(request)
        latency_ms = round((_time.monotonic() - start) * 1000, 2)

        logger.info(
            "mcp_outbound",
            extra={
                "request_id": request_id,
                "status_code": response.status_code,
                "latency_ms": latency_ms,
            },
        )

        response.headers["X-Request-ID"] = request_id
        return response


# ---------------------------------------------------------------------------
# Origin validation (DNS rebinding protection)
# ---------------------------------------------------------------------------

class OriginValidationMiddleware(BaseHTTPMiddleware):
    """Validate Origin header per MCP spec 2026-07-28 §Security & Endpoint."""

    def __init__(self, app: ASGIApp, allowed_origins: list[str] | None = None) -> None:
        super().__init__(app)
        self.allowed_origins = allowed_origins or []

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if request.url.path == "/mcp" and request.method == "POST":
            origin = request.headers.get("origin")
            if origin is not None:
                if self.allowed_origins and origin not in self.allowed_origins:
                    logger.warning("Origin rejected")
                    return JSONResponse(
                        status_code=403,
                        content={"error": "Forbidden: invalid Origin"},
                    )
        return await call_next(request)


# ---------------------------------------------------------------------------
# Per-client rate limiting
# ---------------------------------------------------------------------------

class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforce per-client quota."""

    def __init__(self, app: ASGIApp, limiter: Any = None) -> None:
        super().__init__(app)
        self.limiter = limiter or get_per_client_limiter()

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if request.url.path == "/mcp" and request.method == "POST":
            client_id = request.client.host if request.client else "unknown"
            result = self.limiter.check(client_id)
            if not result["allowed"]:
                resp = JSONResponse(
                    status_code=429,
                    content={
                        "error": "rate_limited",
                        "message": f"Quota exceeded. Retry after {result['retry_after']}s.",
                        "retry_after": result["retry_after"],
                        "limit": result["limit"],
                        "window": result["window"],
                    },
                )
                resp.headers["Retry-After"] = str(int(result["retry_after"]) + 1)
                return resp
        return await call_next(request)


# ---------------------------------------------------------------------------
# MCP server handlers
# ---------------------------------------------------------------------------

async def handle_list_tools(
    context: Any, params: types.PaginatedRequestParams | None
) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="factrail_capabilities",
                description="Discover available capabilities, inputs, fields, sources, versions, and limitations. Call first to inspect current coverage and source state.",
                input_schema={"type": "object", "properties": {
                    "operation": {"type": "string", "enum": ["verify", "assess"]},
                    "capability": {"type": "string", "enum": ["company_fr", "import"]}}, "additionalProperties": False},
                output_schema={"type": "object", "properties": {"capabilities": {"type": "array", "items": {"type": "object"}}}, "required": ["capabilities"]},
                annotations=types.ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False),
            ),
            types.Tool(
                name="factrail_assess",
                description="Assess a structured real-world situation and return an EvidenceEnvelope. Currently supports beta import assessments; results expose source coverage and support levels.",
                input_schema={"type": "object", "properties": {
                    "assessment_type": {"type": "string", "enum": ["import"], "description": "Currently only import is supported."},
                    "parameters": {**AssessImportInput.model_json_schema(), "additionalProperties": False}},
                    "required": ["assessment_type", "parameters"], "additionalProperties": False},
                output_schema=EvidenceEnvelope.model_json_schema(),
                annotations=types.ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
            ),
            types.Tool(
                name="factrail_verify",
                description="Verify structured facts for a supported subject. Currently supports French companies by SIREN or SIRET; returns Evidence with provenance, freshness, coverage, conflicts, and a receipt.",
                input_schema={"type": "object", "properties": {
                    "subject_type": {"type": "string", "enum": ["company_fr"], "description": "Currently only French companies are supported."},
                    "identifier": {"type": "string", "pattern": "^([0-9]{9}|[0-9]{14})$", "description": "A 9-digit SIREN or 14-digit SIRET."},
                    "fields": {"type": "array", "minItems": 1, "items": {"type": "string", "enum": sorted(FIELD_MAP)}, "description": "Optional company fields to resolve; omit for the default set."}},
                    "required": ["subject_type", "identifier"], "additionalProperties": False},
                output_schema=EvidenceEnvelope.model_json_schema(),
                annotations=types.ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
            ),
            types.Tool(
                name="factrail_get_receipt",
                description="Retrieve a stored EvidenceEnvelope by receipt ID. Recomputes its content hash and rejects altered content; this checks integrity, not truth.",
                input_schema={"type": "object", "properties": {"receipt_id": {"type": "string", "pattern": "^fr_[0-9a-f]{64}$", "description": "A FACTRAIL evidence observation ID."}}, "required": ["receipt_id"], "additionalProperties": False},
                output_schema=EvidenceEnvelope.model_json_schema(),
                annotations=types.ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False),
            ),
            types.Tool(
                name="verify_french_company",
                description=(
                    "Compatibility tool for French company lookup by SIREN/SIRET. "
                    "New integrations should prefer factrail_verify for an EvidenceEnvelope."
                ),
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
                annotations=types.ToolAnnotations(
                    read_only_hint=True,
                    destructive_hint=False,
                    idempotent_hint=True,
                    open_world_hint=True,
                ),
            ),
            types.Tool(
                name="assess_import",
                description=(
                    "Compatibility tool for beta EU import assessment. Coverage may be partial or provisional; "
                    "this is not a binding customs result. New integrations should prefer factrail_assess."
                ),
                input_schema={
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
                },
                annotations=types.ToolAnnotations(
                    read_only_hint=True,
                    destructive_hint=False,
                    idempotent_hint=True,
                    open_world_hint=True,
                ),
            ),
            types.Tool(
                name="analyze_company",
                description="Deprecated experimental compatibility-only tool. Financial and credit data sources are unavailable; returned fields are null. Prefer factrail_verify for supported company facts.",
                input_schema={
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
                annotations=types.ToolAnnotations(
                    read_only_hint=True,
                    destructive_hint=False,
                    idempotent_hint=True,
                    open_world_hint=True,
                ),
            ),
        ]
    )


async def handle_call_tool(
    context: Any, params: types.CallToolRequestParams
) -> types.CallToolResult:
    from .request_context import bind_request_context, from_mcp_context, reset_request_context
    token = bind_request_context(from_mcp_context(context))
    try:
        return await _handle_call_tool(context, params)
    finally:
        reset_request_context(token)


async def _handle_call_tool(
    context: Any, params: types.CallToolRequestParams
) -> types.CallToolResult:
    if params.name in ("factrail_verify", "factrail_assess"):
        from .evidence.demand import record_safely
        start = _time.monotonic()
        args = params.arguments or {}
        operation = "verify" if params.name == "factrail_verify" else "assess"
        capability = args.get("subject_type") if operation == "verify" else args.get("assessment_type")
        capability_exists = capability == ("company_fr" if operation == "verify" else "import")
        requested = args.get("fields") if operation == "verify" else list(args.get("parameters", {}).keys()) if isinstance(args.get("parameters"), dict) else []
        if not capability_exists:
            support_status = "unsupported_capability"
        elif operation == "verify":
            from .evidence.company_fr import FIELD_MAP
            support_status = "unsupported_field" if isinstance(requested, list) and any(field not in FIELD_MAP for field in requested) else "supported"
        else:
            support_status = "unsupported_field" if isinstance(requested, list) and any(field not in AssessImportInput.model_fields for field in requested) else "supported"
        result = await (_handle_factrail_verify(params) if operation == "verify" else _handle_factrail_assess(params))
        payload = result.structured_content
        envelope = payload if isinstance(payload, dict) and "coverage" in payload else None
        error = payload.get("error") if isinstance(payload, dict) else None
        if envelope is None and error is None and result.content and isinstance(result.content[0], types.TextContent):
            try:
                error = json.loads(result.content[0].text).get("error")
            except (ValueError, TypeError, AttributeError):
                error = "error"
        coverage = envelope["coverage"] if envelope else {}
        evidence = envelope["evidence"] if envelope else []
        source_outcomes = coverage.get("metadata", {}).get("source_outcomes", {}) if isinstance(coverage, dict) else {}
        source_details = coverage.get("metadata", {}).get("source_details", {}) if isinstance(coverage, dict) else {}
        if error == "upstream" and operation == "verify":
            source_outcomes = {**source_outcomes, "government_registry": "source_error"}
        if isinstance(evidence, list):
            for source in evidence:
                if source.get("source_type") in ("government_registry", "government_bulletin") and source.get("source_status") in ("unavailable", "error"):
                    source_outcomes.setdefault(source["source_type"], "source_error")
        failures = [source for source, state in source_outcomes.items() if state in ("source_error", "source_unavailable")]
        unsupported = not capability_exists
        from .request_context import current_request_context
        client_ctx = current_request_context()
        reasons = coverage.get("metadata", {}).get("unresolved_field_reasons", {}) if isinstance(coverage, dict) else {}
        record_safely(operation=operation, capability=capability, requested_fields=requested,
            outcome=envelope["status"] if envelope else error or "error",
            coverage=coverage.get("level"), unresolved_fields=coverage.get("fields_unresolved", []),
            unsupported_capability=unsupported, source_failures=failures,
            latency_ms=(_time.monotonic() - start) * 1000, origin_class=client_ctx.origin_class,
            client_family=client_ctx.client_family, server_version=client_ctx.server_version,
            request_id=client_ctx.request_id, mcp_client_name=client_ctx.mcp_client_name,
            mcp_client_version=client_ctx.mcp_client_version,
            receipt_id=envelope.get("receipt_id") if envelope else None,
            unresolved_reasons=reasons, source_outcomes=source_outcomes, source_details=source_details,
            request_support_status=support_status, capability_registry_version="2.3")
        return result
    if params.name == "factrail_capabilities":
        return await _handle_factrail_capabilities(params)
    if params.name == "factrail_get_receipt":
        return await _handle_factrail_get_receipt(params)
    if params.name == "verify_french_company":
        return await _handle_verify_french_company(params)
    if params.name == "assess_import":
        return await _handle_assess_import(params)
    if params.name == "analyze_company":
        return await _handle_analyze_company(params)
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=json.dumps({"error": f"Unknown tool: {params.name}"}),
            )
        ]
    )


def _evidence_result(envelope: Any) -> types.CallToolResult:
    payload = envelope.model_dump(mode="json")
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structured_content=payload,
    )


def _evidence_error(code: str, message: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(
        type="text", text=json.dumps({"error": code, "message": message}))],
        structured_content={"error": code, "message": message}, is_error=True)


async def _handle_factrail_verify(params: types.CallToolRequestParams) -> types.CallToolResult:
    from pydantic import ValidationError as ModelValidationError
    from .evidence import VerificationRequest, verify

    try:
        request = VerificationRequest.model_validate(params.arguments or {})
        # Reuse the production cache, source error mapping, and stale fallback.
        if request.subject_type != "company_fr":
            return _evidence_error("unsupported_capability", "unsupported subject_type")
        from .evidence.company_fr import resolve_company_fr
        if not request.identifier.isdigit() or len(request.identifier) not in (9, 14):
            return _evidence_error("invalid_input", "identifier must be a 9-digit SIREN or 14-digit SIRET")
        lookup_result = await _cached_lookup(request.identifier)
        if isinstance(lookup_result, dict):
            return _evidence_error(lookup_result.get("error", "internal"), lookup_result.get("message", "Company lookup failed"))
        envelope = verify(request, resolvers={"company_fr": lambda identifier, fields: resolve_company_fr(
            identifier, fields, lookup=lambda _: lookup_result)})
        return _evidence_result(envelope)
    except (ModelValidationError, ValueError) as exc:
        return _evidence_error("invalid_input", str(exc))
    except Exception as exc:
        logger.error("Unhandled error in factrail_verify")
        return _evidence_error("internal", str(exc))


async def _handle_factrail_assess(params: types.CallToolRequestParams) -> types.CallToolResult:
    from pydantic import ValidationError as ModelValidationError
    from .evidence.service import AssessmentRequest, assess

    args = params.arguments or {}
    if not isinstance(args.get("assessment_type"), str):
        return _evidence_error("invalid_input", "assessment_type is required")
    if args["assessment_type"] != "import":
        return _evidence_error("unsupported_capability", "unsupported assessment_type")
    try:
        request = AssessmentRequest.model_validate(args)
        return _evidence_result(assess(request))
    except ModelValidationError as exc:
        return _evidence_error("invalid_input", str(exc))
    except Exception as exc:
        logger.error("Unhandled error in factrail_assess")
        return _evidence_error("internal", str(exc))


async def _handle_factrail_get_receipt(params: types.CallToolRequestParams) -> types.CallToolResult:
    from .evidence.receipts import ReceiptRepository, ReceiptIntegrityError
    arguments = params.arguments or {}
    receipt_id = arguments.get("receipt_id")
    if set(arguments) != {"receipt_id"} or not isinstance(receipt_id, str) or RECEIPT_ID_PATTERN.fullmatch(receipt_id) is None:
        return _evidence_error("invalid_input", "receipt_id must be fr_ followed by 64 lowercase hex characters")
    try:
        result = ReceiptRepository().get(receipt_id)
        return _evidence_result(result) if result else _evidence_error("not_found", "receipt not found")
    except ReceiptIntegrityError:
        return _evidence_error("integrity_error", "stored receipt content failed integrity verification")
    except Exception as exc:
        logger.error("Unhandled error in factrail_get_receipt")
        return _evidence_error("internal", str(exc))


async def _handle_verify_french_company(
    params: types.CallToolRequestParams
) -> types.CallToolResult:
    arguments = params.arguments or {}
    identifier = arguments.get("identifier", "")
    if not identifier:
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=json.dumps({"error": "identifier is required"}),
                )
            ]
        )

    result = await _cached_lookup(identifier)
    if isinstance(result, dict) and "error" in result:
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=json.dumps(result),
                )
            ]
        )

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False),
            )
        ]
    )


async def _handle_assess_import(
    params: types.CallToolRequestParams
) -> types.CallToolResult:
    from .trade.models import AssessImportInput
    from .trade.service import assess_import

    arguments = params.arguments or {}
    try:
        input = AssessImportInput(**arguments)
    except Exception as exc:
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=json.dumps({
                        "error": "invalid_input",
                        "message": str(exc),
                        "detail": "See error message for the specific validation failure.",
                    }, ensure_ascii=False),
                )
            ]
        )

    try:
        result = assess_import(input)
    except Exception as exc:
        logger.error("Unhandled error in assess_import")
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=json.dumps({
                        "error": "internal",
                        "message": str(exc),
                    }, ensure_ascii=False),
                )
            ]
        )

    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=result.model_dump_json(indent=2, ensure_ascii=False),
            )
        ]
    )


async def _handle_analyze_company(
    params: types.CallToolRequestParams
) -> types.CallToolResult:
    from .company import AnalyzeCompanyInput, analyze_company

    arguments = params.arguments or {}
    try:
        input = AnalyzeCompanyInput(**arguments)
    except Exception as exc:
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=json.dumps({
                        "error": "invalid_input",
                        "message": str(exc),
                    }, ensure_ascii=False),
                )
            ]
        )

    try:
        result = analyze_company(input)
    except Exception as exc:
        logger.error("Unhandled error in analyze_company")
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=json.dumps({
                        "error": "internal",
                        "message": str(exc),
                    }, ensure_ascii=False),
                )
            ]
        )

    payload = result.model_dump(mode="json")
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(payload, indent=2, ensure_ascii=False))], structured_content=payload)


async def _handle_factrail_capabilities(params: types.CallToolRequestParams) -> types.CallToolResult:
    from .evidence.service import capability_registry
    args = params.arguments or {}
    if set(args) - {"operation", "capability"}:
        return _evidence_error("invalid_input", "only operation and capability filters are supported")
    if args.get("operation") not in (None, "verify", "assess") or args.get("capability") not in (None, "company_fr", "import"):
        return _evidence_error("invalid_input", "filter does not match a registered operation or capability")
    items = capability_registry()
    if args.get("operation"):
        items = [item for item in items if item["operation"] == args["operation"]]
    if args.get("capability"):
        items = [item for item in items if item["capability"] == args["capability"]]
    payload = {"capabilities": items, "evidence_schema_version": "1.2"}
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))], structured_content=payload)


async def _cached_lookup(identifier: str) -> FrenchCompany | dict:
    """Try cache first, fall back to live lookup with stale-on-failure."""
    cache = get_cache()

    cached = cache.get(identifier, allow_stale=False)
    if cached is not None:
        logger.info("cache_hit")
        if is_telemetry_enabled():
            _set_cache_status("hit", "cache_only")
        try:
            return FrenchCompany(**cached)
        except Exception:
            pass

    if is_telemetry_enabled():
        _set_cache_status("miss")

    adapter = InseeAdapter()
    try:
        result = adapter.lookup_with_bodacc(identifier)
        cache.set(
            identifier,
            result.model_dump(mode="json"),
            ttl=int(os.environ.get("FACTRAIL_CACHE_TTL", "3600")),
            source="insee_live",
        )
        if is_telemetry_enabled():
            _set_cache_status(upstream="available")
        return result
    except ValidationError as exc:
        logger.info("validation_error")
        if is_telemetry_enabled():
            _set_cache_status(upstream="available")
        return {"error": "validation", "message": str(exc)}
    except NotFoundError as exc:
        logger.info("not_found")
        if is_telemetry_enabled():
            _set_cache_status(upstream="available")
        return {"error": "not_found", "message": str(exc)}
    except UpstreamError as exc:
        logger.warning("upstream_error")
        stale = cache.get(identifier, allow_stale=True)
        if stale is not None:
            logger.info("cache_stale_served")
            if is_telemetry_enabled():
                _set_cache_status("stale", "cache_only")
            try:
                return FrenchCompany(**stale)
            except Exception:
                pass
        if is_telemetry_enabled():
            _set_cache_status(upstream="unavailable")
        return {"error": "upstream", "message": str(exc)}
    except Exception as exc:
        logger.error("Unexpected error in verify_french_company")
        if is_telemetry_enabled():
            _set_cache_status(upstream="unknown")
        return {"error": "internal", "message": str(exc)}


# ---------------------------------------------------------------------------
# Build MCP server
# ---------------------------------------------------------------------------

app = Server(
    "factrail",
    version="2.4.0",
    on_list_tools=handle_list_tools,
    on_call_tool=handle_call_tool,
)


# ---------------------------------------------------------------------------
# Health/readiness endpoints
# ---------------------------------------------------------------------------

async def healthz(request: Request) -> Response:
    return PlainTextResponse("ok")


async def readyz(request: Request) -> Response:
    try:
        cache = get_cache()
        cache._get_conn().execute("SELECT 1").fetchone()
        from .evidence.receipts import ReceiptRepository
        ReceiptRepository(db_path=cache.db_path)
        return JSONResponse({"status": "ready"})
    except Exception:
        logger.error("Readiness check failed")
        return JSONResponse({"status": "not_ready"}, status_code=503)


# ---------------------------------------------------------------------------
# Build Starlette app with middleware + health routes + lifespan
# ---------------------------------------------------------------------------

def _get_allowed_hosts() -> list[str]:
    """Parse FACTRAIL_ALLOWED_HOSTS env var into a list for TransportSecuritySettings.

    Retain localhost development access by default.  The SDK distinguishes
    bare hostnames (matched exactly) from wildcard-port entries suffixed with
    ``:`` (matched against any port on that host), so emit both forms for
    each hostname the operator supplies.
    """
    raw = os.environ.get("FACTRAIL_ALLOWED_HOSTS", "")
    if not raw.strip():
        # Default: keep localhost / loopback working for local development
        # and health-checks without any env var configuration.
        return [
            "localhost",
            "localhost:*",
            "127.0.0.1",
            "127.0.0.1:*",
            "[::1]",
            "[::1]:*",
        ]
    entries: list[str] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        entries.append(token)
        # Emit a wildcard-port variant unless the token already ends with :*
        if not token.endswith(":*"):
            entries.append(token + ":*")
    return entries


def _get_allowed_origins() -> list[str]:
    raw = os.environ.get("FACTRAIL_ALLOWED_ORIGINS", "")
    if not raw:
        return []
    return [o.strip() for o in raw.split(",") if o.strip()]


def _build_transport_security() -> TransportSecuritySettings:
    """Build the MCP SDK transport security configuration.

    - DNS-rebinding protection stays enabled (the default).
    - ``allowed_hosts`` is wired from ``FACTRAIL_ALLOWED_HOSTS`` so that
      public tunnel hostnames such as ``*.trycloudflare.com`` are explicitly
      allowlisted instead of being rejected with 421.
    - ``allowed_origins`` is wired from ``FACTRAIL_ALLOWED_ORIGINS`` and is
      left empty by default so that missing Origin headers (same-origin / API
      clients) continue to be accepted.
    """
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_get_allowed_hosts(),
        allowed_origins=_get_allowed_origins(),
    )


async def glama_claim(request: Request) -> Response:
    """Serve the Glama.ai MCP connector ownership verification claim.

    Glama fetches ``/.well-known/glama.json`` to verify that the operator
    controls the domain listed in the connector's ``remotes``.  The file must
    be publicly reachable and return the exact JSON object Glama issued.
    """
    return JSONResponse(
        content={
            "$schema": "https://glama.ai/mcp/schemas/connector.json",
            "claim": "glama_claim_veyx--rDI8czdOq9DzOObGY52b0L0FXM",
        },
        headers={"Cache-Control": "no-store"},
    )


def build_app() -> Starlette:
    """Build the production Starlette app."""
    mcp_starlette = app.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=_build_transport_security(),
        custom_starlette_routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/readyz", readyz, methods=["GET"]),
            Route("/.well-known/glama.json", glama_claim, methods=["GET"]),
        ],
    )

    # Add middleware for logging, origin validation, and rate limiting.
    # The MCP SDK transport security layer already enforces Host + Origin
    # header validation for the /mcp endpoint using the SDK's official
    # TransportSecurityMiddleware.  Factrail's own OriginValidationMiddleware
    # is retained below so that FACTRAIL_ALLOWED_ORIGINS continues to work
    # independently and existing origin-validation behaviour is unchanged.
    mcp_starlette.user_middleware = [
        Middleware(StructuredLoggingMiddleware),
        Middleware(OriginValidationMiddleware, allowed_origins=_get_allowed_origins()),
        Middleware(RateLimitMiddleware),
        Middleware(TelemetryMiddleware),
    ]

    # Rebuild the middleware stack
    mcp_starlette.build_middleware_stack()

    return mcp_starlette


# ---------------------------------------------------------------------------
# Server runner with graceful shutdown
# ---------------------------------------------------------------------------

class FactrailServer:
    """Production server with graceful shutdown."""

    def __init__(self) -> None:
        self.host = os.environ.get("FACTRAIL_HOST", "127.0.0.1")
        self.port = int(os.environ.get("FACTRAIL_PORT", "8765"))
        self._shutdown_event = asyncio.Event()

    async def run(self) -> None:
        starlette_app = build_app()
        config = uvicorn.Config(
            starlette_app,
            host=self.host,
            port=self.port,
            log_level="info",
        )
        server = uvicorn.Server(config)

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._request_shutdown)
            except NotImplementedError:
                pass

        logger.info("factrail_v2.4.0_listening", extra={"host": self.host, "port": self.port})
        await server.serve()

    def _request_shutdown(self) -> None:
        logger.info("shutdown_signal_received")
        self._shutdown_event.set()


def main() -> None:
    server = FactrailServer()
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
