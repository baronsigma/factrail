"""Pydantic models for the FACTRAIL Trade (assess_import) vertical.

V0.1 — single public MCP tool: assess_import.

All monetary values are in the currency named by the caller
(see LlandedCost.currency).  Provisional/estimated values are
carried with an explicit marker rather than silently rounded.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum, StrEnum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

class AssessmentStatus(str, Enum):
    OK = "ok"
    REVIEW_REQUIRED = "review_required"
    INSUFFICIENT_INFORMATION = "insufficient_information"


# ---------------------------------------------------------------------------
# Input model
# ---------------------------------------------------------------------------

ISO3166_ALPHA2 = frozenset({
    "AD", "AE", "AF", "AG", "AL", "AM", "AO", "AR", "AT", "AU", "AZ",
    "BA", "BB", "BD", "BE", "BF", "BG", "BH", "BI", "BJ", "BN", "BO",
    "BR", "BS", "BT", "BW", "BY", "BZ", "CA", "CH", "CL", "CN", "CO",
    "CR", "CU", "CV", "CY", "CZ", "DE", "DJ", "DK", "DM", "DO", "DZ",
    "EC", "EE", "EG", "ER", "ES", "ET", "FI", "FR", "GA", "GB", "GD",
    "GE", "GH", "GM", "GN", "GQ", "GR", "GT", "GW", "GY", "HK", "HN",
    "HR", "HT", "HU", "ID", "IE", "IL", "IN", "IQ", "IR", "IS", "IT",
    "JM", "JO", "JP", "KE", "KG", "KH", "KM", "KR", "KW", "KZ", "LA",
    "LB", "LC", "LI", "LK", "LR", "LS", "LT", "LU", "LV", "LY", "MA",
    "MC", "MD", "ME", "MG", "MH", "MK", "ML", "MM", "MN", "MO", "MT",
    "MU", "MV", "MW", "MX", "MY", "MZ", "NA", "NE", "NG", "NI", "NL",
    "NO", "NP", "NR", "NZ", "OM", "PA", "PE", "PH", "PK", "PL", "PT",
    "PW", "PY", "QA", "RO", "RS", "RU", "RW", "SA", "SB", "SC", "SD",
    "SE", "SG", "SI", "SK", "SL", "SM", "SN", "SO", "SR", "SS", "ST",
    "SV", "SX", "SY", "SZ", "TC", "TD", "TG", "TH", "TJ", "TL", "TM",
    "TN", "TO", "TR", "TT", "TV", "TZ", "UA", "UG", "US", "UY", "UZ",
    "VA", "VC", "VE", "VG", "VI", "VN", "VU", "WS", "XK", "YE", "ZA",
    "ZM", "ZW",
})

ISO4217_CURRENCIES = frozenset({
    "EUR", "USD", "GBP", "CNY", "JPY", "CHF", "CAD", "AUD", "INR",
    "BRL", "KRW", "SGD", "HKD", "NOK", "SEK", "DKK", "NZD", "MXN",
    "ZAR", "RUB", "TRY", "PLN", "THB", "IDR", "MYR", "PHP",
})

EU_MEMBER_STATES = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
    "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
    "PL", "PT", "RO", "SK", "SI", "ES", "SE",
})


class AssessImportInput(BaseModel):
    """Required + optional inputs for assess_import."""

    product: str = Field(
        ...,
        min_length=3,
        max_length=500,
        examples=["750ml insulated stainless steel drinking bottle"],
    )
    origin_country: str = Field(
        ...,
        min_length=2,
        max_length=2,
        examples=["CN"],
    )
    destination_country: str = Field(
        ...,
        min_length=2,
        max_length=2,
        examples=["FR"],
    )
    quantity: int = Field(
        ...,
        ge=1,
        le=10**9,
        examples=[5000],
    )
    goods_value: float = Field(
        ...,
        ge=0.01,
        le=10**12,
        examples=[13500.0],
    )
    currency: str = Field(
        ...,
        min_length=3,
        max_length=3,
        examples=["EUR"],
    )

    known_hs_code: Optional[str] = Field(
        None,
        min_length=6,
        max_length=10,
        examples=["960100"],
    )
    material: Optional[str] = None
    weight_kg: Optional[float] = Field(None, ge=0.0001, le=10**9)
    dimensions: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    incoterm: Optional[str] = Field(None, max_length=10)
    shipping_mode: Optional[str] = Field(None, max_length=50)
    origin_location: Optional[str] = None
    destination_location: Optional[str] = None
    freight_cost: Optional[float] = Field(None, ge=0.0)
    insurance_cost: Optional[float] = Field(None, ge=0.0)

    @field_validator("origin_country", "destination_country")
    @classmethod
    def _validate_country(cls, v: str) -> str:
        if v.upper() not in ISO3166_ALPHA2:
            raise ValueError(f"Invalid ISO 3166-1 alpha-2 country code: {v!r}")
        return v.upper()

    @field_validator("currency")
    @classmethod
    def _validate_currency(cls, v: str) -> str:
        if v.upper() not in ISO4217_CURRENCIES:
            raise ValueError(f"Unsupported currency code: {v!r}")
        return v.upper()

    @field_validator("incoterm")
    @classmethod
    def _validate_incoterm(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        known = {
            "EXW", "FCA", "CPT", "CIP", "DAP", "DPU", "DDP",
            "FAS", "FOB", "CFR", "CIF",
        }
        if v.upper() not in known:
            raise ValueError(
                f"Unknown Incoterm {v!r}. Expected one of: {', '.join(sorted(known))}"
            )
        return v.upper()

    @model_validator(mode="after")
    def _validate_eu_destination(self) -> "AssessImportInput":
        if self.destination_country not in EU_MEMBER_STATES:
            raise ValueError(
                f"Destination {self.destination_country!r} is not an EU member state. "
                f"V0 only supports EU destinations. Supported: {sorted(EU_MEMBER_STATES)}"
            )
        return self


# ---------------------------------------------------------------------------
# Value provenance
# ---------------------------------------------------------------------------

class ProvenanceStatus(str, Enum):
    VERIFIED = "verified"       # from an authoritative primary source
    DERIVED = "derived"         # computed deterministically from verified inputs
    ESTIMATED = "estimated"     # reasoned estimate with explicit assumptions
    INFERRED = "inferred"       # model/rule-of-thumb guess
    UNAVAILABLE = "unavailable" # authoritative source unreachable / not integrated


class Evidence(BaseModel):
    """A single sourced fact used to build the assessment."""

    value: Any
    status: ProvenanceStatus
    authority: str
    source: str
    url: str
    retrieved_at: datetime
    effective_date: Optional[str] = None
    supports: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    note: Optional[str] = None

    @model_validator(mode="after")
    def _normalise_lists(self) -> "Evidence":
        if isinstance(self.supports, str):
            self.supports = [self.supports]
        return self


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

class ClassificationAlternative(BaseModel):
    code: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    level: Optional[str] = None  # hs / cn / taric


class Classification(BaseModel):
    hs_code: Optional[str] = None
    cn_code: Optional[str] = None
    taric_code: Optional[str] = None
    description: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    alternatives: list[ClassificationAlternative] = Field(default_factory=list)
    reasoning_summary: str = ""
    review_required: bool = False
    evidence: list[Evidence] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Customs
# ---------------------------------------------------------------------------

class Measure(BaseModel):
    name: str
    type: str
    reference: Optional[str] = None
    detail: Optional[str] = None
    evidence: Optional[Evidence] = None


class OriginRule(BaseModel):
    rule: str
    description: Optional[str] = None
    evidence: Optional[Evidence] = None


class PreferenceStatus(str, Enum):
    NO_PREFERENCE = "no_preference"
    PREFERENCE_AVAILABLE = "preference_available"
    PREFERENCE_UNKNOWN = "preference_unknown"


class CustomsValueAssumption(BaseModel):
    component: str
    status: str  # "complete" / "unknown" / "estimated"
    impact: Optional[str] = None
    note: Optional[str] = None


class Customs(BaseModel):
    base_duty_rate_pct: Optional[float] = None
    preferential_rate_pct: Optional[float] = None
    preference_status: PreferenceStatus = PreferenceStatus.NO_PREFERENCE
    applied_rate_type: Optional[str] = None
    applied_duty_rate_pct: Optional[float] = None
    anti_dumping: list[Measure] = Field(default_factory=list)
    additional_measures: list[Measure] = Field(default_factory=list)
    origin_rules: list[OriginRule] = Field(default_factory=list)
    applicable_measures: list[Measure] = Field(default_factory=list)
    estimated_duty: Optional[float] = None
    notes: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    customs_value_complete: bool = False
    customs_value_assumptions: list[CustomsValueAssumption] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Tax
# ---------------------------------------------------------------------------

class Tax(BaseModel):
    import_vat_rate_pct: Optional[float] = None
    estimated_import_vat: Optional[float] = None
    vat_on_duty: bool = True  # EU standard: VAT is levied on (customs value + duty)
    evidence: list[Evidence] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Compliance
# ---------------------------------------------------------------------------

class ComplianceRequirement(BaseModel):
    name: str
    description: str
    applies: bool = True
    evidence: Optional[Evidence] = None
    action: Optional[str] = None


class Compliance(BaseModel):
    requirements: list[ComplianceRequirement] = Field(default_factory=list)
    restrictions: list[ComplianceRequirement] = Field(default_factory=list)
    sanctions_flags: list[str] = Field(default_factory=list)
    documents: list[ComplianceRequirement] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Landed cost
# ---------------------------------------------------------------------------

class LandedCost(BaseModel):
    goods_value: float
    freight: Optional[float] = None
    insurance: Optional[float] = None
    customs_duty: Optional[float] = None
    import_vat: Optional[float] = None
    other_known_costs: Optional[float] = None
    estimated_total: Optional[float] = None
    estimated_cost_per_unit: Optional[float] = None
    currency: str
    completeness: float = Field(ge=0.0, le=1.0, default=1.0)
    assumptions: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Missing information
# ---------------------------------------------------------------------------

class MissingField(BaseModel):
    field: str
    question: str
    impact: str
    field_type: Optional[str] = None


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------

class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskProfile(BaseModel):
    classification: RiskLevel = RiskLevel.LOW
    customs: RiskLevel = RiskLevel.LOW
    compliance: RiskLevel = RiskLevel.LOW
    cost_uncertainty: RiskLevel = RiskLevel.LOW


# ---------------------------------------------------------------------------
# Top-level assessment
# ---------------------------------------------------------------------------

class ImportAssessment(BaseModel):
    assessment_id: str
    status: AssessmentStatus
    requested_at: datetime
    input_summary: dict[str, Any] = Field(default_factory=dict)

    classification: Classification
    customs: Customs
    tax: Tax
    compliance: Compliance
    landed_cost: LandedCost
    missing_information: list[MissingField] = Field(default_factory=list)
    risk: RiskProfile
    sources: list[Evidence] = Field(default_factory=list)

    @field_validator("assessment_id")
    @classmethod
    def _default_id(cls, v: str) -> str:
        return v or uuid.uuid4().hex

    @model_validator(mode="after")
    def _input_summary(self) -> "ImportAssessment":
        if not self.input_summary:
            self.input_summary = {
                "product": self.classification.hs_code or "",
                "origin_country": "",
                "destination_country": "",
                "quantity": 0,
                "currency": "",
            }
        return self
