"""
Customs duty engine for FACTRAIL Trade — TARIC-authoritative, curated-fallback.

V0.2: Replaces the curated-only approach with a two-tier engine:

  1. Primary: EU TARIC / Access2Markets authoritative data via TaricStore.
  2. Fallback: curated reference data (EU_MFN_DUTY, EU_FTA_MAP) when the
     authoritative source is unavailable for a given HS code / origin combo.

Fallback data is explicitly marked as "fallback", never "verified", with
lower confidence and a warning.

Also fixes:
  - Customs model: preference_status, nullable preferential_rate_pct,
    applied_rate_type, applied_duty_rate_pct
  - Customs-value completeness: when freight/insurance unknown, duty estimate
    is flagged as based on incomplete customs value
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from .landed_cost import build_landed_cost
from .models import (
    Customs,
    CustomsValueAssumption,
    Evidence,
    Measure,
    OriginRule,
    PreferenceStatus,
    ProvenanceStatus,
)
from .taric_store import (
    TaricStore,
    TaricUnavailable,
    find_mfn_duty,
    find_preference,
    get_default_store,
    parse_tariff_rate,
    lookup_import_measures,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Curated fallback data (secondary, explicitly marked)
# ---------------------------------------------------------------------------

EU_MFN_DUTY: dict[str, dict[str, Any]] = {
    "9617": {
        "mfn_duty_pct": 3.0,
        "note": "EU MFN duty for heading 9617 — representative rate from EU MFN tariff schedule.",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "authority": "European Commission — EU MFN tariff schedule (curated reference)",
        "source": "EU MFN tariff reference (heading 9617)",
        "effective_date": "2024",
    },
    "9619": {
        "mfn_duty_pct": 5.0,
        "note": "EU MFN duty for heading 9619.",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "authority": "European Commission — EU MFN tariff schedule (curated reference)",
        "source": "EU MFN tariff reference (heading 9619)",
        "effective_date": "2024",
    },
    "4202": {
        "mfn_duty_pct": 3.0,
        "note": "EU MFN duty for heading 4202.",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "authority": "European Commission — EU MFN tariff schedule (curated reference)",
        "source": "EU MFN tariff reference (heading 4202)",
        "effective_date": "2024",
    },
    "8507": {
        "mfn_duty_pct": 3.0,
        "note": "EU MFN duty for heading 8507.",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "authority": "European Commission — EU MFN tariff schedule (curated reference)",
        "source": "EU MFN tariff reference (heading 8507)",
        "effective_date": "2024",
    },
    "7307": {
        "mfn_duty_pct": 3.0,
        "note": "EU MFN duty for heading 7307.",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "authority": "European Commission — EU MFN tariff schedule (curated reference)",
        "source": "EU MFN tariff reference (heading 7307)",
        "effective_date": "2024",
    },
    "8413": {
        "mfn_duty_pct": 3.0,
        "note": "EU MFN duty for heading 8413.",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "authority": "European Commission — EU MFN tariff schedule (curated reference)",
        "source": "EU MFN tariff reference (heading 8413)",
        "effective_date": "2024",
    },
    "8479": {
        "mfn_duty_pct": 3.0,
        "note": "EU MFN duty for heading 8479.",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "authority": "European Commission — EU MFN tariff schedule (curated reference)",
        "source": "EU MFN tariff reference (heading 8479)",
        "effective_date": "2024",
    },
    "8483": {
        "mfn_duty_pct": 3.0,
        "note": "EU MFN duty for heading 8483.",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "authority": "European Commission — EU MFN tariff schedule (curated reference)",
        "source": "EU MFN tariff reference (heading 8483)",
        "effective_date": "2024",
    },
    "8544": {
        "mfn_duty_pct": 3.0,
        "note": "EU MFN duty for heading 8544.",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "authority": "European Commission — EU MFN tariff schedule (curated reference)",
        "source": "EU MFN tariff reference (heading 8544)",
        "effective_date": "2024",
    },
    "8708": {
        "mfn_duty_pct": 2.5,
        "note": "EU MFN duty for heading 8708.",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "authority": "European Commission — EU MFN tariff schedule (curated reference)",
        "source": "EU MFN tariff reference (heading 8708)",
        "effective_date": "2024",
    },
    "9401": {
        "mfn_duty_pct": 3.0,
        "note": "EU MFN duty for heading 9401.",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "authority": "European Commission — EU MFN tariff schedule (curated reference)",
        "source": "EU MFN tariff reference (heading 9401)",
        "effective_date": "2024",
    },
    "6203": {
        "mfn_duty_pct": 12.0,
        "note": "EU MFN duty for heading 6203.",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "authority": "European Commission — EU MFN tariff schedule (curated reference)",
        "source": "EU MFN tariff reference (heading 6203)",
        "effective_date": "2024",
    },
    "6204": {
        "mfn_duty_pct": 12.0,
        "note": "EU MFN duty for heading 6204.",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "authority": "European Commission — EU MFN tariff schedule (curated reference)",
        "source": "EU MFN tariff reference (heading 6204)",
        "effective_date": "2024",
    },
    "9606": {
        "mfn_duty_pct": 3.0,
        "note": "EU MFN duty for heading 9606.",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "authority": "European Commission — EU MFN tariff schedule (curated reference)",
        "source": "EU MFN tariff reference (heading 9606)",
        "effective_date": "2024",
    },
    "3926": {
        "mfn_duty_pct": 6.0,
        "note": "EU MFN duty for heading 3926.",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "authority": "European Commission — EU MFN tariff schedule (curated reference)",
        "source": "EU MFN tariff reference (heading 3926)",
        "effective_date": "2024",
    },
}

# EU preferential/FTA map (curated reference — secondary)
EU_FTA_MAP: dict[str, dict[str, Any]] = {
    "CN": {
        "agreement": "No comprehensive EU-China FTA",
        "treatment": "MFN applies",
        "preferential_rate_pct": None,
        "url": "https://policy.trade.ec.europa.eu/eu-trade-relationships-country/china",
        "note": "No comprehensive FTA between EU and China. MFN duty applies.",
    },
    "JP": {
        "agreement": "EU-Japan Economic Partnership Agreement (EPA)",
        "treatment": "Preferential — many industrial goods duty-free",
        "preferential_rate_pct": 0.0,
        "url": "https://policy.trade.ec.europa.eu/eu-trade-relationships-country/japan",
        "note": "EU-Japan EPA — preferential rates may apply for qualifying goods.",
    },
    "KR": {
        "agreement": "EU-Korea FTA",
        "treatment": "Preferential",
        "preferential_rate_pct": 0.0,
        "url": "https://policy.trade.ec.europa.eu/eu-trade-relationships-country/republic-korea",
        "note": "EU-Korea FTA — preferential rates may apply.",
    },
    "GB": {
        "agreement": "EU-UK Trade and Cooperation Agreement (TCA)",
        "treatment": "Preferential — qualifying goods",
        "preferential_rate_pct": 0.0,
        "url": "https://policy.trade.ec.europa.eu/eu-trade-relationships-country/united-kingdom",
        "note": "EU-UK TCA — preferential rates for qualifying goods.",
    },
    "CA": {
        "agreement": "EU-Canada Comprehensive Economic and Trade Agreement (CETA)",
        "treatment": "Preferential",
        "preferential_rate_pct": 0.0,
        "url": "https://policy.trade.ec.europa.eu/eu-trade-relationships-country/canada",
        "note": "CETA — preferential rates for qualifying goods.",
    },
    "NO": {
        "agreement": "EEA / EFTA",
        "treatment": "Duty-free (EEA)",
        "preferential_rate_pct": 0.0,
        "url": "https://policy.trade.ec.europa.eu/eu-trade-relationships-country/norway",
        "note": "Norway is in EEA — goods in free circulation move duty-free.",
    },
    "CH": {
        "agreement": "EU-Switzerland FTA",
        "treatment": "Preferential",
        "preferential_rate_pct": 0.0,
        "url": "https://policy.trade.ec.europa.eu/eu-trade-relationships-country/switzerland",
        "note": "EU-Switzerland FTA — preferential rates for qualifying goods.",
    },
    "US": {
        "agreement": "No EU-US FTA",
        "treatment": "MFN applies",
        "preferential_rate_pct": None,
        "url": "https://policy.trade.ec.europa.eu/eu-trade-relationships-country/united-states",
        "note": "No FTA between EU and US. MFN duty applies.",
    },
    "IN": {
        "agreement": "No EU-India FTA (negotiations ongoing)",
        "treatment": "MFN applies",
        "preferential_rate_pct": None,
        "url": "https://policy.trade.ec.europa.eu/eu-trade-relationships-country/india",
        "note": "No FTA. MFN duty applies.",
    },
    "ZA": {
        "agreement": "EU-South Africa FTA (Trade, Development and Cooperation Agreement)",
        "treatment": "Preferential",
        "preferential_rate_pct": 0.0,
        "url": "https://policy.trade.ec.europa.eu/eu-trade-relationships-country/south-africa",
        "note": "EU-SA trade agreement — preferential rates for qualifying goods.",
    },
}

EU_MEMBER_STATES = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
    "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
    "PL", "PT", "RO", "SK", "SI", "ES", "SE",
})


def build_customs_block(
    classification: Any,
    origin_country: str,
    destination_country: str,
    goods_value: float,
    quantity: int,
    freight_cost: Optional[float],
    insurance_cost: Optional[float],
    evidence_ledger: list[Evidence],
    *,
    taric_store: Optional[TaricStore] = None,
) -> Customs:
    """Build the Customs block for an import assessment.

    Two-tier approach:
      1. Try authoritative TARIC store (Access2Markets / DG TAXUD).
      2. Fall back to curated reference data if authoritative data unavailable.

    Return a Customs block with:
      - preference_status
      - preferential_rate_pct (nullable — None means no preference)
      - applied_rate_type
      - applied_duty_rate_pct
      - applicable_measures
      - customs_value_complete / customs_value_assumptions
    """
    hs_code = classification.hs_code or ""
    heading = hs_code[:4] if len(hs_code) >= 4 else hs_code

    customs = Customs(
        base_duty_rate_pct=None,
        preferential_rate_pct=None,
        preference_status=PreferenceStatus.NO_PREFERENCE,
        applied_rate_type=None,
        applied_duty_rate_pct=None,
        estimated_duty=None,
        applicable_measures=[],
        origin_rules=[],
        customs_value_complete=False,
        customs_value_assumptions=[],
        notes=[],
        evidence=[],
    )

    ev = evidence_ledger

    # ------------------------------------------------------------------
    # 1. Determine customs value completeness
    # ------------------------------------------------------------------
    customs_value_components: list[str] = ["goods_value"]
    if freight_cost is not None:
        customs_value_components.append("freight")
    else:
        customs.customs_value_assumptions.append(
            CustomsValueAssumption(
                component="freight",
                status="unknown",
                impact="Duty base may be understated; actual duty may be higher.",
                note="Freight cost not supplied. Customs value (CIF basis) is incomplete.",
            )
        )
    if insurance_cost is not None:
        customs_value_components.append("insurance")
    else:
        customs.customs_value_assumptions.append(
            CustomsValueAssumption(
                component="insurance",
                status="unknown",
                impact="Duty base may be understated; actual duty may be higher.",
                note="Insurance cost not supplied. Customs value (CIF basis) is incomplete.",
            )
        )

    customs.customs_value_complete = (
        freight_cost is not None and insurance_cost is not None
    )

    if not customs.customs_value_complete:
        customs.notes.append(
            "Customs value (CIF basis) is incomplete — freight and/or insurance not supplied. "
            "Duty estimate is based on goods value alone and may be understated."
        )

    # Compute customs value for duty calculation
    customs_value = goods_value
    if freight_cost is not None:
        customs_value += freight_cost
    if insurance_cost is not None:
        customs_value += insurance_cost

    # ------------------------------------------------------------------
    # 2. Authoritative TARIC lookup
    # ------------------------------------------------------------------
    authoritative = False
    source_used = "unknown"
    taric_measures: list[dict] = []
    fallback_used = False

    try:
        store = taric_store or get_default_store()
        result = store.get_measures(hs_code, origin_country, destination_country)
        taric_measures = result.get("measures", [])
        source_used = result.get("_source", "unknown")
        logger.info(
            "taric_authoritative_lookup_ok",
            extra={"source": source_used, "measures": len(taric_measures)},
        )
        if taric_measures:
            authoritative = True
        else:
            # Fetch succeeded but returned no parsed measures — treat as unavailable
            logger.warning(
                "taric_authoritative_empty_measures",
            )
            authoritative = False
            fallback_used = True
    except TaricUnavailable as exc:
        logger.warning(
            "taric_authoritative_unavailable",
        )
        authoritative = False
        fallback_used = True
    except Exception as exc:
        logger.error(
            "taric_lookup_error",
        )
        authoritative = False
        fallback_used = True

    # ------------------------------------------------------------------
    # 3. Extract duty from authoritative measures
    # ------------------------------------------------------------------
    mfn_measure = find_mfn_duty(taric_measures)
    pref_measure = find_preference(taric_measures)

    if authoritative and mfn_measure:
        base_rate = parse_tariff_rate(mfn_measure.get("tariff"))
        customs.base_duty_rate_pct = base_rate
        customs.applied_rate_type = "third_country_mfn"
        customs.applied_duty_rate_pct = base_rate
        customs.preference_status = (
            PreferenceStatus.PREFERENCE_AVAILABLE
            if pref_measure
            else PreferenceStatus.NO_PREFERENCE
        )
        if pref_measure:
            pref_rate = parse_tariff_rate(pref_measure.get("tariff"))
            customs.preferential_rate_pct = pref_rate

        # Build evidence for authoritative duty
        ev_record = Evidence(
            value=f"{base_rate}%",
            status=ProvenanceStatus.VERIFIED,
            authority="European Commission — DG TAXUD TARIC database / Access2Markets",
            source=f"EU Access2Markets results for {hs_code} ({origin_country} → {destination_country}) — third-country duty",
            url=f"https://trade.ec.europa.eu/access-to-markets/en/results?product={hs_code}&origin={origin_country}&destination={destination_country}",
            retrieved_at=datetime.now(timezone.utc),
            effective_date=result.get("version_date", "current"),
            supports=["duty", "tariff", "customs_value"],
            confidence=0.95,
            note=(
                f"Third-country (MFN) duty retrieved from EU Access2Markets "
                f"(version {result.get('version', 'unknown')}, {result.get('version_date', 'unknown')}). "
                f"Source: DG TAXUD TARIC database. "
                f"Regulation: {mfn_measure.get('eu_law_reference', 'see A2M page')}. "
                f"Origin area: {mfn_measure.get('origin_area', 'ERGA OMNES')}."
            ),
        )
        customs.evidence.append(ev_record)
        ev.append(ev_record)

        # Evidence for the source page itself
        source_ev = Evidence(
            value="EU Access2Markets tariff results page",
            status=ProvenanceStatus.VERIFIED,
            authority="European Commission — Access2Markets (built on DG TAXUD TARIC)",
            source="EU Access2Markets — official EU trade policy portal",
            url=f"https://trade.ec.europa.eu/access-to-markets/en/results?product={hs_code}&origin={origin_country}&destination={destination_country}",
            retrieved_at=datetime.now(timezone.utc),
            effective_date=result.get("version_date", "current"),
            supports=["duty", "tariff", "source"],
            confidence=0.9,
            note=f"Access2Markets version {result.get('version', 'unknown')} ({result.get('version_date', 'unknown')}). EU tariffs sourced from DG TAXUD TARIC database (updated daily).",
        )
        if source_ev not in customs.evidence:
            customs.evidence.append(source_ev)
        ev.append(source_ev)

        # Attach applicable measures
        for m in taric_measures:
            measure = Measure(
                name=m.get("measure_type", "Unknown measure"),
                type=m.get("measure_kind", "other"),
                reference=m.get("eu_law_reference"),
                detail=_format_measure_detail(m),
                evidence=_measure_evidence(m, hs_code, origin_country, destination_country, result),
            )
            customs.applicable_measures.append(measure)

            # Review-required measures
            if m.get("review_required"):
                customs.notes.append(
                    f"Measure '{m.get('measure_type')}' has conditions that require review "
                    f"before import — see conditions/footnotes in applicable_measures."
                )

        # Evidence for each measure
        for m in taric_measures:
            me = _measure_evidence(m, hs_code, origin_country, destination_country, result)
            if me and me not in customs.evidence:
                customs.evidence.append(me)
                ev.append(me)

    elif fallback_used:
        # ------------------------------------------------------------------
        # 4. Curated fallback (secondary, NOT verified)
        # ------------------------------------------------------------------
        entry = EU_MFN_DUTY.get(heading)
        if entry:
            base_rate = entry["mfn_duty_pct"]
            customs.base_duty_rate_pct = base_rate
            customs.applied_rate_type = "mfn_curated_fallback"
            customs.applied_duty_rate_pct = base_rate
            customs.preference_status = PreferenceStatus.NO_PREFERENCE

            fallback_ev = Evidence(
                value=f"{base_rate}% (curated fallback)",
                status=ProvenanceStatus.ESTIMATED,
                authority=entry["authority"],
                source=entry["source"] + " — CURATED FALLBACK, not live-verified",
                url=entry["taric_url"],
                retrieved_at=datetime.now(timezone.utc),
                effective_date=entry.get("effective_date"),
                supports=["duty", "tariff"],
                confidence=0.4,
                note=(
                    f"MFN duty from curated reference data for heading {heading}. "
                    f"THIS IS A FALLBACK — the authoritative TARIC lookup was unavailable "
                    f"({fallback_used}). The rate has NOT been verified against the current "
                    f"EU TARIC database. The actual rate may differ. "
                    f"Verify against: {entry['taric_url']}"
                ),
            )
            customs.evidence.append(fallback_ev)
            ev.append(fallback_ev)

            customs.notes.append(
                f"Customs duty rate ({base_rate}%) is from curated reference data — "
                f"NOT verified against the current EU TARIC database. "
                f"Authoritative data unavailable for this heading ({heading}). "
                f"Verify before import: {entry['taric_url']}"
            )
        else:
            customs.notes.append(
                f"No MFN duty data available for heading {heading} — neither from "
                f"authoritative TARIC nor from curated reference data."
            )

        # Pref/FTA lookup from curated map
        fta = EU_FTA_MAP.get(origin_country)
        if fta:
            pref_ev = Evidence(
                value=f"{fta['treatment']}",
                status=ProvenanceStatus.INFERRED,
                authority="European Commission — DG TRADE",
                source=f"EU FTA reference — {fta['agreement']} (curated)",
                url=fta["url"],
                retrieved_at=datetime.now(timezone.utc),
                effective_date="2024",
                supports=["preferential", "origin"],
                confidence=0.5,
                note=f"FTA status for {origin_country}: {fta['note']}. Curated reference — not verified against current EU trade policy.",
            )
            customs.evidence.append(pref_ev)
            ev.append(pref_ev)

            if fta["preferential_rate_pct"] is not None:
                customs.preference_status = PreferenceStatus.PREFERENCE_AVAILABLE
                customs.preferential_rate_pct = fta["preferential_rate_pct"]
                customs.notes.append(
                    f"Preferential agreement: {fta['agreement']}. "
                    f"Preferential rate may be {fta['preferential_rate_pct']}% for qualifying goods — "
                    f"rules of origin must be verified."
                )
                customs.origin_rules.append(
                    OriginRule(
                        rule="Preferential origin",
                        description=(
                            f"To benefit from the preferential rate under {fta['agreement']}, "
                            f"the goods must satisfy the product-specific rules of origin. "
                            f"Verify against: {fta['url']}"
                        ),
                        evidence=pref_ev,
                    )
                )
            else:
                customs.notes.append(
                    f"No preferential agreement: {fta['agreement']}. MFN duty applies."
                )

    # ------------------------------------------------------------------
    # 5. Compute estimated duty
    # ------------------------------------------------------------------
    if customs.applied_duty_rate_pct is not None:
        duty = round(customs_value * customs.applied_duty_rate_pct / 100.0, 2)
        customs.estimated_duty = duty

        duty_note_parts = [f"Estimated duty at {customs.applied_duty_rate_pct}% of customs value"]
        if not customs.customs_value_complete:
            duty_note_parts.append("(INCOMPLETE — based on goods value only; freight/insurance not included)")
        duty_note_parts.append(f"= {customs_value} × {customs.applied_duty_rate_pct}% = {duty} {goods_value and 'EUR'}")
        customs.notes.append(" ".join(duty_note_parts))

        # Duty evidence with completeness flag
        duty_ev = Evidence(
            value=f"{duty} EUR",
            status=ProvenanceStatus.DERIVED,
            authority="FACTRAIL customs engine",
            source="Duty estimated from applied tariff rate × customs value",
            url=f"https://factrail.online/trade/customs",
            retrieved_at=datetime.now(timezone.utc),
            supports=["duty", "customs_value", "estimated"],
            confidence=0.6 if customs.customs_value_complete else 0.35,
            note=(
                f"Estimated duty = {customs_value} (customs value) × {customs.applied_duty_rate_pct}% "
                f"= {duty} EUR. "
                + ("Customs value is complete (goods + freight + insurance)." if customs.customs_value_complete
                   else "CUSTOMS VALUE INCOMPLETE — freight and/or insurance not supplied. "
                        "This estimate may be understated. Provide freight and insurance for a complete calculation.")
            ),
        )
        customs.evidence.append(duty_ev)
        ev.append(duty_ev)
    else:
        customs.notes.append("No duty rate available — cannot estimate duty.")

    # ------------------------------------------------------------------
    # 6. Compliance / sanctions note
    # ------------------------------------------------------------------
    # For China specifically, the A2M page warns about sanctions on some products.
    # We note this generically.
    if origin_country == "CN":
        san_note = (
            "EU sanctions exist on some products originating in China. "
            "The DG TAXUD TARIC database integrates applicable sanctions. "
            "Verify sanctions applicability for this specific product before import. "
            "See: https://sanctionsmap.eu/ and https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp"
        )
        customs.notes.append(san_note)
        san_ev = Evidence(
            value="Sanctions check required",
            status=ProvenanceStatus.INFERRED,
            authority="European Union — Consolidated Sanctions List / DG TAXUD TARIC",
            source="EU sanctions on some Chinese products — integrated in TARIC",
            url="https://sanctionsmap.eu/",
            retrieved_at=datetime.now(timezone.utc),
            supports=["sanctions", "compliance"],
            confidence=0.3,
            note="Generic note — specific sanctions applicability depends on the product. Verify against TARIC for this HS code.",
        )
        customs.evidence.append(san_ev)
        ev.append(san_ev)

    _attach_origin_rules_from_taric(
        customs, taric_measures, origin_country, destination_country, ev
    )

    return customs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_measure_detail(m: dict) -> Optional[str]:
    """Format a measure's detail for display."""
    parts = []
    if m.get("tariff"):
        parts.append(f"Tariff: {m['tariff']}")
    footnotes = m.get("footnotes", [])
    if footnotes:
        parts.append(f"Footnotes: {len(footnotes)} footnote(s) — see evidence for full text")
    conditions = m.get("conditions", [])
    if conditions:
        parts.append(f"Conditions: {len(conditions)} condition(s) — see evidence for details")
    if m.get("eu_law_reference"):
        parts.append(f"EU law: {m['eu_law_reference']}")
    return " | ".join(parts) if parts else None


def _measure_evidence(
    m: dict,
    hs_code: str,
    origin: str,
    destination: str,
    result: dict,
) -> Optional[Evidence]:
    """Build an Evidence record for a single measure."""
    notes: list[str] = []
    if m.get("footnotes"):
        for fn in m["footnotes"][:3]:
            notes.append(f"[{fn['code']}] {fn['text']}")
        if len(m["footnotes"]) > 3:
            notes.append(f"... and {len(m['footnotes']) - 3} more footnotes")
    if m.get("conditions"):
        for c in m["conditions"][:2]:
            notes.append(f"Condition {c['code']}: {c['action']} — {c['certificate_or_document']}")

    return Evidence(
        value=f"{m.get('measure_type', 'Unknown')} — {m.get('tariff', 'N/A')}",
        status=ProvenanceStatus.VERIFIED,
        authority="European Commission — DG TAXUD TARIC database / Access2Markets",
        source=f"EU Access2Markets measure: {m.get('measure_type', '')} ({m.get('origin_area', '')})",
        url=f"https://trade.ec.europa.eu/access-to-markets/en/results?product={hs_code}&origin={origin}&destination={destination}",
        retrieved_at=datetime.now(timezone.utc),
        effective_date=result.get("version_date", "current"),
        supports=["duty", "tariff", "measure", "compliance"],
        confidence=0.9 if m.get("review_required") else 0.95,
        note="\\n".join(notes) if notes else f"Measure: {m.get('measure_type', '')}. EU law: {m.get('eu_law_reference', '')}.",
    )


def _attach_origin_rules_from_taric(
    customs: Customs,
    measures: list[dict],
    origin: str,
    destination: str,
    ev: list[Evidence],
) -> None:
    """Attach origin rules from TARIC measures when present."""
    # If we have a preference measure, attach origin rule
    pref = find_preference(measures)
    if pref:
        rule_ev = Evidence(
            value=f"Preferential rate: {pref.get('tariff', 'N/A')}",
            status=ProvenanceStatus.VERIFIED,
            authority="European Commission — DG TAXUD TARIC / DG TRADE",
            source=f"EU Access2Markets — preferential tariff for {origin} → {destination}",
            url=f"https://trade.ec.europa.eu/access-to-markets/en/results?product={pref.get('hs_code', '')}&origin={origin}&destination={destination}",
            retrieved_at=datetime.now(timezone.utc),
            supports=["preferential", "origin", "rules_of_origin"],
            confidence=0.9,
            note=f"Preferential tariff available: {pref.get('tariff')}. Verify rules of origin for this product. EU law: {pref.get('eu_law_reference', '')}.",
        )
        customs.origin_rules.append(
            OriginRule(
                rule="Preferential origin",
                description=(
                    f"Preferential tariff of {pref.get('tariff')} available for "
                    f"{origin} → {destination}. Goods must satisfy the product-specific "
                    f"rules of origin under the relevant EU trade agreement. "
                    f"Verify: https://trade.ec.europa.eu/access-to-markets/en/results"
                ),
                evidence=rule_ev,
            )
        )
        if rule_ev not in ev:
            ev.append(rule_ev)


# ---------------------------------------------------------------------------
# VAT update — authoritative rates with last-verified metadata
# ---------------------------------------------------------------------------

PRAGMA_FOREIGN_KEYS = "PRAGMA foreign_keys = ON"


def build_vat(
    destination_country: str,
    customs_value: float,
    customs_duty: Optional[float],
    evidence_ledger: list[Evidence],
) -> Any:
    """Build the Tax block (VAT) for an import assessment.

    Uses authoritative VAT rates with last-verified metadata.
    """
    from .vat import EU_VAT_RATES, eu_vat_rate

    from .models import Evidence, ProvenanceStatus, Tax

    rate = eu_vat_rate(destination_country)
    tax = Tax(
        import_vat_rate_pct=rate,
        vat_on_duty=True,
        evidence=[],
    )

    if rate is None:
        tax.evidence.append(Evidence(
            value="unknown",
            status=ProvenanceStatus.UNAVAILABLE,
            authority="FACTRAIL (V0)",
            source=f"EU VAT rate reference data not available for {destination_country}",
            url="https://taxation-customs.ec.europa.eu/taxation/vat/vat-directive/vat-rates_en",
            retrieved_at=datetime.now(timezone.utc),
            supports=["tax"],
            confidence=0.0,
            note="Standard VAT rate not available for this destination in V0 reference data.",
        ))
        evidence_ledger.append(tax.evidence[-1])
        return tax

    # Build VAT rate evidence with provenance
    rate_entry = EU_VAT_RATES.get(destination_country)
    if rate_entry:
        # Tuple is (rate, source_url, source_note, verified_date)
        _rate_val, source_url, source_note, verified_date = rate_entry
    else:
        source_url = "https://taxation-customs.ec.europa.eu/taxation/vat/vat-directive/vat-rates_en"
        source_note = f"Standard VAT rate for {destination_country}"
        verified_date = "2024/2025"
        status_label = "reference"

    iso3_lookup = {
        "FR": "FRA", "DE": "DEU", "IT": "ITA", "ES": "ESP",
        "AT": "AUT", "BE": "BEL", "BG": "BGR", "HR": "HRV",
        "CY": "CYP", "CZ": "CZE", "DK": "DNK", "EE": "EST",
        "FI": "FIN", "GR": "GRC", "HU": "HUN", "IE": "IRL",
        "LV": "LVA", "LT": "LTU", "LU": "LUX", "MT": "MLT",
        "NL": "NLD", "PL": "POL", "PT": "PRT", "RO": "ROU",
        "SK": "SVK", "SI": "SVN", "SE": "SWE",
    }
    iso3 = iso3_lookup.get(destination_country, destination_country)

    vat_evidence = Evidence(
        value=f"{rate}%",
        status=ProvenanceStatus.VERIFIED,
        authority=f"National tax authority ({iso3}) / European Commission",
        source=f"EU VAT reference data — {source_note}",
        url=source_url,
        retrieved_at=datetime.now(timezone.utc),
        effective_date=verified_date,
        supports=["tax", "vat_rate"],
        confidence=1.0,
        note=(
            f"Standard VAT rate for {destination_country} ({iso3}): {rate}%. "
            f"Source: {source_note}. "
            f"Last verified/retrieved: {verified_date}. "
            f"URL: {source_url}. "
            f"Reference data (not scraped live in default path)."
        ),
    )
    tax.evidence.append(vat_evidence)
    evidence_ledger.append(vat_evidence)

    # Compute VAT
    base = customs_value
    if customs_duty is not None:
        base += customs_duty
        tax.evidence.append(Evidence(
            value=f"{rate}% x ({customs_value} + {customs_duty})",
            status=ProvenanceStatus.DERIVED,
            authority="EU VAT directive — taxable base includes customs duty",
            source="EU VAT on imports — taxable base = customs value + duty (unless IOSS/OSS)",
            url="https://taxation-customs.ec.europa.eu/taxation/vat/vat-directive/vat-rates_en",
            retrieved_at=datetime.now(timezone.utc),
            supports=["tax", "vat_base"],
            confidence=1.0,
            note="EU VAT applies to (customs value + customs duty) in the standard import case.",
        ))
        evidence_ledger.append(tax.evidence[-1])
    else:
        tax.evidence.append(Evidence(
            value=f"{rate}% x {customs_value} (duty unknown)",
            status=ProvenanceStatus.ESTIMATED,
            authority="EU VAT directive — standard base is customs value + duty",
            source="EU VAT on imports",
            url="https://taxation-customs.ec.europa.eu/taxation/vat/vat-directive/vat-rates_en",
            retrieved_at=datetime.now(timezone.utc),
            supports=["tax", "vat_base"],
            confidence=0.5,
            note="Duty unknown; VAT computed on goods value alone as a conservative estimate. Actual VAT may be higher if duty applies.",
        ))
        evidence_ledger.append(tax.evidence[-1])

    tax.estimated_import_vat = round(base * rate / 100.0, 2)
    return tax
