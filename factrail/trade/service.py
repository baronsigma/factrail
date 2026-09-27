"""Service layer for FACTRAIL Trade V0.1.

Orchestrates the assessment pipeline.  All functions are pure except where
explicitly noted (logging).  No module-level side effects.

Pipeline order (as required by spec):
  assess_import
      ↓
  product resolver
      ↓
  classification engine
      ↓
  tariff/measures engine
      ↓
  origin engine
      ↓
  compliance engine
      ↓
  landed cost engine
      ↓
  risk/confidence engine
      ↓
  evidence ledger
"""

from __future__ import annotations

from datetime import datetime, timezone
from logging import getLogger
from typing import Any, Optional

from .classification import build_classification, add_classification_evidence
from .compliance import build_compliance
from .customs import (
    build_customs_block as build_customs,
    build_vat as _build_vat,
)
from .landed_cost import build_landed_cost
from .models import (
    AssessImportInput,
    Classification,
    Compliance,
    Customs,
    CustomsValueAssumption,
    Evidence,
    ImportAssessment,
    LandedCost,
    MissingField,
    PreferenceStatus,
    ProvenanceStatus,
    RiskLevel,
    RiskProfile,
    Tax,
)
from .taric_store import get_default_store

logger = getLogger(__name__)


def assess_import(
    input: AssessImportInput,
    *,
    evidence: Optional[list[Evidence]] = None,
    check_live_taric: bool = False,
    check_live_access2markets: bool = False,
    check_live_sanctions: bool = False,
    check_live_bti: bool = False,
    check_live_vat: bool = False,
) -> ImportAssessment:
    """Run the full import assessment pipeline.

    Parameters
    ----------
    input:
        Validated caller input.
    evidence:
        Optional mutable list to collect evidence records.  If None, a fresh
        list is created.
    check_live_taric:
        When True and a browser/HTTP adapter is available, attempt a live
        TARIC lookup for MFN duty and measures.  In V0 the stub returns
        unavailable unless the caller injects live results via the evidence
        ledger.
    check_live_access2markets:
        When True, attempt live Access2Markets lookup for preferential rates
        and rules of origin.  V0 stub returns unavailable.
    check_live_sanctions:
        When True, check EU consolidated sanctions list for the origin country.
        V0 stub returns unavailable.
    check_live_bti:
        When True, query national BTI database for binding classification.
        V0 stub returns unavailable.
    check_live_vat:
        When True, attempt live VAT rate retrieval from national authority.
        V0 delegates to vat.py reference data (works for all 23 EU states;
        unavailable for non-EU destinations).
    """
    ev = evidence if evidence is not None else []
    now = datetime.now(timezone.utc)
    requested_at = now

    # ------------------------------------------------------------------
    # 1. Product resolver
    # ------------------------------------------------------------------
    hs6 = input.known_hs_code
    material = input.material

    logger.info(
        "trade_assessment_start",
        extra={
            "origin": input.origin_country,
            "destination": input.destination_country,
            "quantity": input.quantity,
            "hs_supplied": bool(input.known_hs_code),
        },
    )

    # ------------------------------------------------------------------
    # 2. Classification engine
    # ------------------------------------------------------------------
    classification = build_classification(
        product=input.product,
        material=material,
        known_hs_code=input.known_hs_code,
        origin_country=input.origin_country,
        destination_country=input.destination_country,
        evidence_ledger=ev,
    )
    add_classification_evidence(classification, hs6 or classification.hs_code, ev)

    # ------------------------------------------------------------------
    # 3. Tariff / measures engine
    # ------------------------------------------------------------------
    customs = build_customs(
        classification=classification,
        origin_country=input.origin_country,
        destination_country=input.destination_country,
        goods_value=input.goods_value,
        quantity=input.quantity,
        freight_cost=input.freight_cost,
        insurance_cost=input.insurance_cost,
        evidence_ledger=ev,
        taric_store=get_default_store(),
    )

    # ------------------------------------------------------------------
    # 4. Origin engine (V0: rules of origin summary — embedded in customs)
    # ------------------------------------------------------------------
    # Origin rules are already attached by build_customs in V0.
    # Future: a dedicated origin_engine module with FTA rules-of-origin
    # determination.  For V0 we keep the stub summary in customs.origin_rules.

    # ------------------------------------------------------------------
    # 4b. Live source adapters (V0 stubs — called when requested)
    # ------------------------------------------------------------------
    if check_live_taric:
        from .sources import TaricSource
        taric_ev = TaricSource().lookup_duty(
            hs_code=hs6 or classification.hs_code or "",
            origin_country=input.origin_country,
            destination_country=input.destination_country,
        )
        if taric_ev not in ev:
            ev.append(taric_ev)

    if check_live_access2markets:
        from .sources import Access2MarketsSource
        a2m_ev = Access2MarketsSource().lookup_market_access(
            origin_country=input.origin_country,
            destination_country=input.destination_country,
            hs_code=hs6 or classification.hs_code or "",
        )
        if a2m_ev not in ev:
            ev.append(a2m_ev)

    if check_live_sanctions:
        from .sources import SanctionsSource
        san_ev = SanctionsSource().check(
            origin_country=input.origin_country,
            destination_country=input.destination_country,
        )
        if san_ev not in ev:
            ev.append(san_ev)

    if check_live_bti:
        from .sources import EbtiSource
        bti_ev = EbtiSource().lookup_bti(
            country=input.destination_country,
            hs_code=hs6 or classification.hs_code or "",
        )
        if bti_ev not in ev:
            ev.append(bti_ev)

    # ------------------------------------------------------------------
    # 5. Compliance engine (V0 stub)
    # ------------------------------------------------------------------
    compliance = build_compliance(
        product=input.product,
        hs6=hs6 or classification.hs_code,
        origin_country=input.origin_country,
        destination_country=input.destination_country,
        rules=None,
        evidence_ledger=ev,
    )

    # ------------------------------------------------------------------
    # 6. VAT engine
    # ------------------------------------------------------------------
    # Build the customs value for VAT purposes (EU: goods + freight + insurance,
    # or goods alone if freight/insurance unknown).  Then apply VAT rate.
    customs_value_for_vat = input.goods_value
    if input.freight_cost is not None:
        customs_value_for_vat += input.freight_cost
    if input.insurance_cost is not None:
        customs_value_for_vat += input.insurance_cost

    tax = _build_vat(
        destination_country=input.destination_country,
        customs_value=customs_value_for_vat,
        customs_duty=customs.estimated_duty,
        evidence_ledger=ev,
    )

    # ------------------------------------------------------------------
    # 7. Landed cost engine
    # ------------------------------------------------------------------
    landed = build_landed_cost(
        goods_value=input.goods_value,
        currency=input.currency,
        quantity=input.quantity,
        freight_cost=input.freight_cost,
        insurance_cost=input.insurance_cost,
        customs_duty=customs.estimated_duty,
        import_vat=tax.estimated_import_vat,
        other_known_costs=None,
        evidence_ledger=ev,
    )

    # ------------------------------------------------------------------
    # 8. Missing information (explicit questions for the user/agent)
    # ------------------------------------------------------------------
    missing: list[MissingField] = []

    if input.material is None or (isinstance(input.material, str) and not input.material.strip()):
        missing.append(MissingField(
            field="material",
            question="What is the primary material of the product? (e.g. stainless steel, plastic, glass, aluminium)",
            impact="May change tariff classification and duty rate.",
            field_type="string",
        ))

    if input.weight_kg is None:
        missing.append(MissingField(
            field="weight_kg",
            question="What is the gross weight per unit in kilograms?",
            impact="May affect duty calculation if a weight-based rate applies.",
            field_type="number",
        ))

    if input.dimensions is None:
        missing.append(MissingField(
            field="dimensions",
            question="What are the product dimensions (length × width × height, cm)?",
            impact="May affect classification in some headings and transport cost estimates.",
            field_type="string",
        ))

    # Intent / use-case questions
    product_lower = input.product.lower()
    if any(k in product_lower for k in ["bottle", "flask", "cup", "mug", "container",
                                         "lunch box", "food ", "drink", "beverage",
                                         "water bottle", "thermos"]):
        missing.append(MissingField(
            field="food_contact",
            question="Is the product intended to come into contact with food or beverages?",
            impact="Food-contact materials regulations (EC 1935/2004 and relevant framework regs) may apply.",
            field_type="boolean",
        ))

    if any(k in product_lower for k in ["electric", "battery", "lithium", "li-ion",
                                         "usb", "cord", "plug", "heated", "charging",
                                         "motor", "electronic"]):
        missing.append(MissingField(
            field="electrical",
            question="Is the product electrically powered? Does it contain a battery (e.g. lithium-ion)?",
            impact="If yes, batteries regulation, shipping restrictions (IATA/ADR), CE marking, "
                    "and electrical safety directives may apply. Lithium batteries may require UN38.3 test summary.",
            field_type="boolean",
        ))

    if any(k in product_lower for k in ["toy", "child", "baby", "infant", "kid", "junior"]):
        missing.append(MissingField(
            field="intended_user",
            question="Is the product intended for use by children?",
            impact="If yes, EU Toy Safety Directive (2009/48/EC) and additional safety requirements may apply.",
            field_type="boolean",
        ))

    if "cosmetic" in product_lower or "beauty" in product_lower or "skincare" in product_lower:
        missing.append(MissingField(
            field="cosmetic",
            question="Is the product a cosmetic or personal care product?",
            impact="If yes, EU Cosmetics Regulation (EC 1223/2009) applies — notification via CPNP, "
                    "PIF, ingredient labelling, and responsible person required.",
            field_type="boolean",
        ))

    if "medicine" in product_lower or "pharmaceutical" in product_lower:
        missing.append(MissingField(
            field="pharmaceutical",
            question="Is the product a medicine or pharmaceutical product?",
            impact="If yes, EMA / national medicines agency rules, marketing authorisation, and "
                    "pharmaceutical customs procedures apply.",
            field_type="boolean",
        ))

    # ------------------------------------------------------------------
    # 9. Risk / confidence engine
    # ------------------------------------------------------------------
    risk = RiskProfile()

    # Classification risk
    if classification.review_required or classification.confidence < 0.5:
        risk.classification = RiskLevel.HIGH
    elif classification.confidence < 0.8:
        risk.classification = RiskLevel.MEDIUM
    else:
        risk.classification = RiskLevel.LOW

    # Customs risk
    if customs.estimated_duty is None or customs.base_duty_rate_pct is None:
        risk.customs = RiskLevel.HIGH
    elif customs.anti_dumping or customs.additional_measures:
        risk.customs = RiskLevel.HIGH
    elif customs.preferential_rate_pct is not None and customs.base_duty_rate_pct is not None:
        # Preferential available and known MFN rate — low unless anti-dumping etc.
        risk.customs = RiskLevel.LOW
    else:
        risk.customs = RiskLevel.MEDIUM

    # Compliance risk
    if compliance.warnings:
        risk.compliance = RiskLevel.MEDIUM
    if compliance.restrictions:
        risk.compliance = RiskLevel.HIGH
    else:
        risk.compliance = RiskLevel.LOW

    # Cost uncertainty
    if landed.completeness < 0.7:
        risk.cost_uncertainty = RiskLevel.HIGH
    elif landed.completeness < 0.9:
        risk.cost_uncertainty = RiskLevel.MEDIUM
    else:
        risk.cost_uncertainty = RiskLevel.LOW

    # ------------------------------------------------------------------
    # 10. Status
    # ------------------------------------------------------------------
    # A known HS code from the caller is the strongest signal — if we have
    # that and classification is not flagged for review, the assessment is
    # "ok" even if some secondary attributes (weight, dimensions) are missing.
    has_known_hs = bool(input.known_hs_code)

    if classification.review_required and not missing:
        status = "review_required"
    elif classification.confidence < 0.3 and not missing:
        status = "review_required"
    elif missing and not has_known_hs:
        # Missing information only lowers status when we had to infer
        # classification from the product description.
        status = "insufficient_information"
    else:
        status = "ok"

    # ------------------------------------------------------------------
    # 11. Input summary (for telemetry)
    # ------------------------------------------------------------------
    input_summary: dict[str, Any] = {
        "product_summary": (input.product[:120] if input.product else ""),
        "origin_country": input.origin_country,
        "destination_country": input.destination_country,
        "quantity": input.quantity,
        "goods_value": input.goods_value,
        "currency": input.currency,
        "hs_supplied": bool(input.known_hs_code),
    }

    # ------------------------------------------------------------------
    # 12. Evidence ledger: attach top-level sources list (deduplicated)
    # ------------------------------------------------------------------
    # Dedup evidence by content hash to avoid repeats when the same Evidence
    # object is referenced from multiple sub-blocks.
    seen: set[str] = set()
    deduped: list[Evidence] = []
    for e in ev:
        key = (
            str(e.value),
            e.status.value,
            e.authority,
            e.source,
            e.url,
            str(e.confidence),
        )
        if key not in seen:
            seen.add(key)
            deduped.append(e)

    return ImportAssessment(
        assessment_id="",
        status=status,
        requested_at=requested_at,
        input_summary=input_summary,
        classification=classification,
        customs=customs,
        tax=tax,
        compliance=compliance,
        landed_cost=landed,
        missing_information=missing,
        risk=risk,
        sources=deduped,
    )
