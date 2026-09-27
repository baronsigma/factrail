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
    score: float = Field(ge=0.0, le=100.0, description="0-100 health score")
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
    score: float = Field(ge=-1.0, le=1.0)
    impact: str
    evidence_url: Optional[str] = None


class CreditRiskAssessment(BaseModel):
    """Credit risk analysis."""
    score: float = Field(ge=0.0, le=100.0, description="Risk score: 0=lowest risk, 100=highest risk")
    rating: CreditRating
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
    market_trends: str = ""
    growth_potential: str = ""  # low/medium/high
    disruption_risk: str = ""  # low/medium/high


# ---------------------------------------------------------------------------
# Business Risk
# ---------------------------------------------------------------------------

class BusinessRisk(BaseModel):
    concentration: str  # low/medium/high
    supply_chain: str  # low/medium/high
    regulatory: str  # low/medium/high
    financial: str  # low/medium/high
    reputation: str  # low/medium/high


# ---------------------------------------------------------------------------
# Executive Team Quality
# ---------------------------------------------------------------------------

class ExecutiveProfile(BaseModel):
    name: str
    role: str
    experience_years: int
    risk_score: float = Field(ge=0.0, le=10.0)


class ExecutiveTeam(BaseModel):
    size: int
    avg_experience: float
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
    company_name: str
    siren: Optional[str] = None

    financial_health: FinancialHealth
    credit_risk: CreditRiskAssessment
    market_position: MarketPosition
    business_risk: BusinessRisk
    executive_team: ExecutiveTeam

    sources: list[dict] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, description="Overall analysis confidence")
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Service Layer
# ---------------------------------------------------------------------------

def analyze_company(
    input: AnalyzeCompanyInput,
    *,
    evidence: Optional[list] = None,
) -> AnalyzeCompanyOutput:
    """Analyze a company's financial health, credit risk, and market position.

    V0.1 returns curated data structures for known French companies.
    Live data integration is stubbed for V0.
    """
    ev = evidence if evidence is not None else []
    now = datetime.now(timezone.utc)

    # Resolver: determine company identity
    company_name = input.company_name or "Unknown Company"
    siren = input.siren

    # V0: Return placeholder analysis with available reference data
    # Real implementation would query live databases

    # Basic financial health (placeholder values)
    financial_health = FinancialHealth(
        score=50.0,  # Medium health
        rating=CreditRating.BB,
        debt_to_equity=None,
        current_ratio=None,
        quick_ratio=None,
        interest_coverage=None,
        profitability_margin=None,
        revenue_growth_yoy=None,
        assets_turnover=None,
    )

    # Credit risk assessment (stub)
    credit_risk = CreditRiskAssessment(
        score=45.0,
        rating=CreditRating.BB_MINUS,
        factors=[],
        pd_5y=None,
        lgd=None,
        ead=None,
        recommendation="Monitor credit exposure; consider credit insurance for large exposure",
    )

    # Market position (stub)
    market_position = MarketPosition(
        market_share=None,
        sector_rank=None,
        competitive_advantages=[],
        market_trends="Unknown - data not integrated in V0",
        growth_potential="medium",
        disruption_risk="medium",
    )

    # Business risk profile (stub)
    business_risk = BusinessRisk(
        concentration="medium",
        supply_chain="medium",
        regulatory="low",
        financial="medium",
        reputation="low",
    )

    # Executive team (stub)
    executive_team = ExecutiveTeam(
        size=1,
        avg_experience=0.0,
        turnover_rate=None,
        profiles=[],
    )

    # Determine confidence
    has_siren = siren is not None
    has_name = company_name is not None
    confidence = 0.3 if not has_siren else 0.5

    # Status
    if has_siren or has_name:
        status = AnalysisStatus.PARTIAL
        notes = ["V0 returns placeholder data; live integration required"]
    else:
        status = AnalysisStatus.REVIEW_REQUIRED
        notes = ["Provide SIREN or company_name for analysis"]

    return AnalyzeCompanyOutput(
        siren=siren,
        company_name=company_name if has_name else "Unknown",
        financial_health=financial_health,
        credit_risk=credit_risk,
        market_position=market_position,
        business_risk=business_risk,
        executive_team=executive_team,
        sources=ev,
        confidence=confidence,
        notes=notes,
        status=status,
    )


# ---------------------------------------------------------------------------
# Legacy alias for backward compatibility
# ---------------------------------------------------------------------------

CompanyAnalysis = AnalyzeCompanyOutput
