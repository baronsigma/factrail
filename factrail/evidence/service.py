"""Registry-based deterministic verification router."""
from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .company_fr import resolve_company_fr
from .models import EvidenceEnvelope
from .receipts import ReceiptRepository
from .trade import assess_import_evidence
from factrail.trade.models import AssessImportInput


class VerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_type: str
    identifier: str
    fields: list[str] | None = Field(default=None, min_length=1)


Resolver = Callable[[str, list[str] | None], EvidenceEnvelope]
RESOLVERS: dict[str, Resolver] = {"company_fr": resolve_company_fr}


class AssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessment_type: str
    parameters: AssessImportInput


AssessmentResolver = Callable[[AssessImportInput], EvidenceEnvelope]
ASSESSMENT_RESOLVERS: dict[str, AssessmentResolver] = {"import": assess_import_evidence}


def verify(request: VerificationRequest, repository: ReceiptRepository | None = None,
           resolvers: dict[str, Resolver] | None = None) -> EvidenceEnvelope:
    resolver = (resolvers or RESOLVERS).get(request.subject_type)
    if resolver is None:
        raise ValueError(f"unsupported subject_type: {request.subject_type}")
    return (repository or ReceiptRepository()).save(resolver(request.identifier, request.fields))


def assess(request: AssessmentRequest, repository: ReceiptRepository | None = None,
           resolvers: dict[str, AssessmentResolver] | None = None) -> EvidenceEnvelope:
    resolver = (resolvers or ASSESSMENT_RESOLVERS).get(request.assessment_type)
    if resolver is None:
        raise ValueError(f"unsupported assessment_type: {request.assessment_type}")
    return (repository or ReceiptRepository()).save(resolver(request.parameters))


def capability_registry() -> list[dict]:
    """Public discovery metadata derived from the registered resolver maps."""
    from .company_fr import DEFAULT_FIELDS, FIELD_MAP
    company_fields = sorted(FIELD_MAP)
    company = {
        "capability": "company_fr", "operation": "verify", "subject_type": "company_fr",
        "description": "Verify French company registration facts by SIREN or SIRET.",
        "capability_version": "1.0", "evidence_schema_version": "1.2",
        "input_schema": {"type": "object", "properties": {
            "subject_type": {"const": "company_fr"},
            "identifier": {"type": "string", "pattern": "^([0-9]{9}|[0-9]{14})$"},
            "fields": {"type": "array", "items": {"type": "string", "enum": company_fields}},
        }, "required": ["subject_type", "identifier"], "additionalProperties": False},
        "required_inputs": ["identifier"], "optional_inputs": ["fields"],
        "fields": company_fields, "default_fields": list(DEFAULT_FIELDS),
        "sources": ["INSEE Sirene", "BODACC"],
        "limitations": ["French companies only", "Current registration fields come from INSEE; BODACC notices are historical context."],
    }
    trade_fields = ["origin_country", "destination_country", "product_description", "hs_code", "cn_code", "taric_code", "customs_measures", "goods_value", "currency", "quantity", "base_duty_rate_pct", "import_vat_rate_pct", "estimated_duty", "estimated_import_vat", "estimated_total", "compliance_requirement", "compliance_findings"]
    assessment_schema = AssessImportInput.model_json_schema()
    assessment_required = [name for name, info in AssessImportInput.model_fields.items() if info.is_required()]
    assessment_optional = [name for name in AssessImportInput.model_fields if name not in assessment_required]
    trade = {
        "capability": "import", "resolver": "trade_import", "operation": "assess", "assessment_type": "import",
        "description": "Assess a structured import scenario using the Trade engine.",
        "capability_version": "1.0", "evidence_schema_version": "1.2",
        "input_schema": {"type": "object", "properties": {"assessment_type": {"const": "import"}, "parameters": assessment_schema}, "required": ["assessment_type", "parameters"], "additionalProperties": False},
        "required_inputs": assessment_required, "optional_inputs": assessment_optional, "fields": trade_fields,
        "sources": ["Official European Commission DG TAXUD TARIC snapshots (primary when installed locally)", "Access2Markets (secondary evidence only)", "curated FACTRAIL tariff references (explicit fallback)", "curated VAT reference data"],
        "limitations": ["An official TARIC snapshot must be ingested locally; automatic CIRCABC acquisition is not available.", "Access2Markets is secondary and is never labelled official TARIC evidence.", "Snapshot reference dates may lag later changes; failed refreshes mark the last-good snapshot stale.", "Classification candidates can be provisional; official nomenclature data is used to check code precision only when a snapshot is installed.", "Preferences, measure conditions, and country-group applicability can remain unresolved in partial snapshots.", "Curated tariff rates and VAT are not authoritative customs or tax determinations."],
    }
    available = ([company] if "company_fr" in RESOLVERS else []) + ([trade] if "import" in ASSESSMENT_RESOLVERS else [])
    return available
