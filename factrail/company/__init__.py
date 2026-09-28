"""FACTRAIL Company Intelligence — pre-company-analysis vertical (V0.1).

Single public MCP tool: analyze_company.

Architecture:
  analyze_company
      |
      +-- company resolver
      |
      +-- financial health engine
      |
      +-- credit risk engine
      |
      +-- market position engine
      |
      +-- risk assessment engine
      |
      +-- evidence ledger
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Re-export models for public API
# ---------------------------------------------------------------------------

__all__ = [
    "AnalysisStatus",
    "CreditRating",
    "FinancialHealth",
    "CreditRiskFactor",
    "CreditRiskAssessment",
    "MarketPosition",
    "BusinessRisk",
    "ExecutiveProfile",
    "ExecutiveTeam",
    "AnalyzeCompanyInput",
    "AnalyzeCompanyOutput",
    "analyze_company",
    "CompanyAnalysis",  # Alias for backward compatibility
]


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

class AnalysisStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    REVIEW_REQUIRED = "review_required"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


# ---------------------------------------------------------------------------
# Credit Rating Classes
# ---------------------------------------------------------------------------

class CreditRating(str, Enum):
    AAA = "AAA"
    AA_PLUS = "AA+"
    AA = "AA"
    AA_MINUS = "AA-"
    A_PLUS = "A+"
    A = "A"
    A_MINUS = "A-"
    BBB_PLUS = "BBB+"
    BBB = "BBB"
    BBB_MINUS = "BBB-"
    BB_PLUS = "BB+"
    BB = "BB"
    BB_MINUS = "BB-"
    B_PLUS = "B+"
    B = "B"
    B_MINUS = "B-"
    CCC_PLUS = "CCC+"
    CCC = "CCC"
    CC = "CC"
    C = "C"
    D = "D"
    NR = "NR"  # Not Rated


# ---------------------------------------------------------------------------
# Financial Health Indicators
# ---------------------------------------------------------------------------

class FinancialHealth(BaseModel):
    """Financial health scoring."""
    score: Optional[float] = Field(None, ge=0.0, le=100.0, description="Unavailable until a financial data source is integrated")
    rating: Optional[CreditRating] = None
    debt_to_equity: Optional[float] = Field(None, description="Debt-to-equity ratio")
    current_ratio: Optional[float] = Field(None, description="Current assets / current liabilities")
    quick_ratio: Optional[float] = Field(None, description="Quick ratio (acid-test)")
    interest_coverage: Optional[float] = Field(None, description="EBIT / interest expense")
    profitability_margin: Optional[float] = Field(None, description="Net profit margin %")
    revenue_growth_yoy: Optional[float] = Field(None, description="Revenue YoY growth %")
    assets_turnover: Optional[float] = Field(None, description="Revenue / total assets")


# ---------------------------------------------------------------------------
# Credit Risk Factors
# ---------------------------------------------------------------------------

class CreditRiskFactor(BaseModel):
    factor: str
    score: Optional[float] = Field(None, ge=-1.0, le=1.0)
    impact: str
    evidence_url: Optional[str] = None


class CreditRiskAssessment(BaseModel):
    """Credit risk analysis."""
    score: Optional[float] = Field(None, ge=0.0, le=100.0, description="Unavailable until a credit source is integrated")
    rating: Optional[CreditRating] = None
    factors: list[CreditRiskFactor] = Field(default_factory=list)
    pd_5y: Optional[float] = Field(None, description="5-year probability of default %")
    lgd: Optional[float] = Field(None, description="Loss given default %")
    ead: Optional[float] = Field(None, description="Exposure at default (currency)")
    recommendation: Optional[str] = None


# ---------------------------------------------------------------------------
# Market Position
# ---------------------------------------------------------------------------

class MarketPosition(BaseModel):
    """Market position analysis."""
    market_share: Optional[float] = Field(None, description="Market share % in primary market")
    sector_rank: Optional[int] = Field(None, description="Rank in sector (1=leader)")
    competitive_advantages: list[str] = Field(default_factory=list)
    market_trends: Optional[str] = None
    growth_potential: Optional[str] = None
    disruption_risk: Optional[str] = None


# ---------------------------------------------------------------------------
# Business Risk
# ---------------------------------------------------------------------------

class BusinessRisk(BaseModel):
    concentration: Optional[str] = None
    supply_chain: Optional[str] = None
    regulatory: Optional[str] = None
    financial: Optional[str] = None
    reputation: Optional[str] = None


# ---------------------------------------------------------------------------
# Executive Team Quality
# ---------------------------------------------------------------------------

class ExecutiveProfile(BaseModel):
    name: str
    role: str
    experience_years: int
    risk_score: float = Field(ge=0.0, le=10.0)


class ExecutiveTeam(BaseModel):
    size: Optional[int] = None
    avg_experience: Optional[float] = None
    turnover_rate: Optional[float] = None
    profiles: list[ExecutiveProfile] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Input Model
# ---------------------------------------------------------------------------

class AnalyzeCompanyInput(BaseModel):
    """Input for company analysis."""
    siren: Optional[str] = Field(None, description="SIREN (9-digit) or SIRET identifier")
    company_name: Optional[str] = Field(None, description="Company legal name")
    country_code: Optional[str] = Field(None, description="ISO 3166-1 alpha-2 country code")

    @field_validator("siren")
    @classmethod
    def validate_siren(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip().upper()
        if not v.isdigit() or len(v) not in (9, 14):
            raise ValueError("SIREN must be 9 digits or SIRET must be 14 digits")
        return v


# ---------------------------------------------------------------------------
# Top-Level Output
# ---------------------------------------------------------------------------

class AnalyzeCompanyOutput(BaseModel):
    """Company analysis result."""
    analysis_id: str = Field(default_factory=lambda: str(uuid4()).replace("-", ""))
    status: AnalysisStatus
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    company_name: Optional[str] = None
    siren: Optional[str] = None

    financial_health: FinancialHealth
    credit_risk: CreditRiskAssessment
    market_position: MarketPosition
    business_risk: BusinessRisk
    executive_team: ExecutiveTeam

    sources: list[dict] = Field(default_factory=list)
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0, description="Unavailable; no analysis sources are integrated")
    notes: list[str] = Field(default_factory=list)
    unavailable: dict[str, dict[str, str]] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Service Layer
# ---------------------------------------------------------------------------

def analyze_company(
    input: AnalyzeCompanyInput,
    *,
    evidence: Optional[list] = None,
) -> AnalyzeCompanyOutput:
    """Compatibility response; company financial/risk sources are not integrated."""
    ev = evidence if evidence is not None else []
    return AnalyzeCompanyOutput(
        siren=input.siren,
        company_name=input.company_name,
        financial_health=FinancialHealth(),
        credit_risk=CreditRiskAssessment(),
        market_position=MarketPosition(),
        business_risk=BusinessRisk(),
        executive_team=ExecutiveTeam(profiles=[]),
        sources=ev,
        confidence=None,
        notes=["Compatibility-only response. Company financial, credit, market, risk, and executive data sources are not integrated."],
        unavailable={name: {"reason": "source_not_integrated"} for name in (
            "financial_health", "credit_score", "credit_rating", "market_position",
            "business_risk", "executive_team")},
        status=AnalysisStatus.INSUFFICIENT_EVIDENCE,
    )


# ---------------------------------------------------------------------------
# Legacy alias for backward compatibility
# ---------------------------------------------------------------------------

CompanyAnalysis = AnalyzeCompanyOutput
