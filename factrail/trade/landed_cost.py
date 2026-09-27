"""Deterministic landed-cost engine for FACTRAIL Trade V0.1.

The engine computes a landed cost from the values the caller actually
provides.  It never invents freight, insurance, or other cost figures.
Where a cost component is unknown, it is left as null and the completeness
score is reduced.

Deterministic: same inputs → same outputs, regardless of external state.
Testable: each cost component is independently derivable.

EU customs valuation basis (V0 simplified):
  - Customs value for duty = CIF (Cost, Insurance, Freight) in the
    general EU case when incoterm is not DDP.
  - When no freight/insurance data is supplied, duty is computed on the
    goods value alone (conservative estimate) with a note in assumptions.

VAT base (EU standard import):
  - taxable base = customs value + customs duty (+ other levies where
    applicable).  For V0: where duty is unknown, VAT is computed on
    goods value only with a note.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .models import (
    Evidence,
    LandedCost,
    ProvenanceStatus,
)


def build_landed_cost(
    goods_value: float,
    currency: str,
    quantity: int,
    freight_cost: Optional[float],
    insurance_cost: Optional[float],
    customs_duty: Optional[float],
    import_vat: Optional[float],
    other_known_costs: Optional[float],
    evidence_ledger: list[Evidence],
) -> LandedCost:
    """Build the LandedCost block for an import assessment.

    All values are in *currency*.  If a component is unknown, it is null.
    The estimated_total is computed only if all major components are known
    (or can be reasonably estimated); otherwise it remains null and
    completeness is reduced.
    """
    lc = LandedCost(
        goods_value=goods_value,
        freight=freight_cost,
        insurance=insurance_cost,
        customs_duty=customs_duty,
        import_vat=import_vat,
        other_known_costs=other_known_costs,
        estimated_total=None,
        estimated_cost_per_unit=None,
        currency=currency,
        completeness=1.0,
        assumptions=[],
        evidence=[],
    )

    assumptions: list[str] = []
    missing_components: list[str] = []

    # Default other_known_costs to 0 if not provided
    if other_known_costs is None:
        other_known_costs = 0.0
        assumptions.append("No other known costs supplied; treated as 0.")

    # 1. Completeness scoring
    if freight_cost is None:
        missing_components.append("freight")
        assumptions.append(
            "Freight cost not supplied. Landed cost estimate is incomplete."
        )
    if insurance_cost is None:
        missing_components.append("insurance")
        assumptions.append(
            "Insurance cost not supplied. Landed cost estimate is incomplete."
        )
    if customs_duty is None:
        missing_components.append("customs_duty")
        assumptions.append(
            "Customs duty not available from V0 tariff engine. "
            "Landed cost estimate is incomplete."
        )
    if import_vat is None:
        missing_components.append("import_vat")
        assumptions.append(
            "Import VAT not available. Landed cost estimate is incomplete."
        )

    # Completeness: 1.0 minus a penalty for each missing major component
    penalty = len(missing_components) * 0.15
    lc.completeness = max(0.0, min(1.0, 1.0 - penalty))

    # 2. Estimated total — only if we have enough to compute it
    if customs_duty is not None and import_vat is not None:
        # Total = goods + freight + insurance + duty + VAT + other
        total = goods_value
        if freight_cost is not None:
            total += freight_cost
        if insurance_cost is not None:
            total += insurance_cost
        total += customs_duty
        total += import_vat
        total += other_known_costs
        lc.estimated_total = round(total, 2)
        lc.assumptions.append(
            f"Estimated total computed as goods value + freight "
            f"{'+ freight' if freight_cost else '+ no freight'} + "
            f"insurance {'+ insurance' if insurance_cost else '+ no insurance'} + "
            f"customs duty + import VAT + other known costs."
        )
    else:
        lc.assumptions.append(
            "Insufficient data to compute estimated total landed cost."
        )

    # 3. Cost per unit — only if total is known and quantity > 0
    if lc.estimated_total is not None and quantity and quantity > 0:
        lc.estimated_cost_per_unit = round(lc.estimated_total / quantity, 4)

    # 4. Evidence — one record per component that is present
    def _component_evidence(
        label: str,
        value: Optional[float],
        status: ProvenanceStatus,
        authority: str,
        source: str,
        url: str,
        supports: list[str],
        confidence: float,
        note: str,
    ) -> Optional[Evidence]:
        if value is None:
            return None
        ev = Evidence(
            value=f"{value} {currency}",
            status=status,
            authority=authority,
            source=source,
            url=url,
            retrieved_at=datetime.now(timezone.utc),
            supports=supports,
            confidence=confidence,
            note=note,
        )
        return ev

    comps = [
        ("goods_value", goods_value,
         ProvenanceStatus.VERIFIED, "Caller-provided", "Caller input",
         "https://factrail.online/trade/landed-cost",
         ["landed_cost", "goods_value"], 1.0,
         "Goods value as declared by caller."),
        ("freight", freight_cost,
         ProvenanceStatus.ESTIMATED if freight_cost else ProvenanceStatus.UNAVAILABLE,
         "Caller / freight estimate", "Freight cost",
         "https://factrail.online/trade/landed-cost",
         ["landed_cost", "freight"], 0.5 if freight_cost else 0.0,
         "Freight cost as supplied by caller or estimated."),
        ("insurance", insurance_cost,
         ProvenanceStatus.ESTIMATED if insurance_cost else ProvenanceStatus.UNAVAILABLE,
         "Caller / insurance estimate", "Insurance cost",
         "https://factrail.online/trade/landed-cost",
         ["landed_cost", "insurance"], 0.5 if insurance_cost else 0.0,
         "Insurance cost as supplied by caller or estimated."),
        ("customs_duty", customs_duty,
         ProvenanceStatus.DERIVED if customs_duty else ProvenanceStatus.UNAVAILABLE,
         "EU customs — tariff engine", "EU MFN tariff rate applied to customs value",
         "https://ec.europa.eu/taxation_customs/dds2/taric",
         ["landed_cost", "customs_duty"], 0.6 if customs_duty else 0.0,
         "Customs duty estimated from EU MFN rate applied to customs value."),
        ("import_vat", import_vat,
         ProvenanceStatus.DERIVED if import_vat else ProvenanceStatus.UNAVAILABLE,
         "EU VAT directive", "Import VAT computed on (customs value + duty)",
         "https://ec.europa.eu/taxation_customs/vat_el/html/vat_import_en",
         ["landed_cost", "vat"], 0.6 if import_vat else 0.0,
         "Import VAT estimated from EU VAT rate applied to taxable base."),
        ("estimated_total", lc.estimated_total,
         ProvenanceStatus.DERIVED if lc.estimated_total else ProvenanceStatus.UNAVAILABLE,
         "FACTRAIL landed-cost engine", "Deterministic sum of provided + estimated components",
         "https://factrail.online/trade/landed-cost",
         ["landed_cost", "total"], 0.7 if lc.estimated_total else 0.0,
         "Estimated total landed cost computed from provided components."),
    ]

    for _, value, status, authority, source, url, supports, confidence, note in comps:
        ev = _component_evidence(
            "", value, status, authority, source, url, supports, confidence, note
        )
        if ev is not None:
            lc.evidence.append(ev)

    lc.assumptions = list(dict.fromkeys(assumptions))  # dedup, preserve order
    evidence_ledger.extend(lc.evidence)

    return lc
