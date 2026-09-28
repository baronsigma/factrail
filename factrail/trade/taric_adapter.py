"""Legacy curated tariff references and an unavailable live TARIC adapter.

Official Commission TARIC data is handled by :mod:`official_taric_store` after a
local snapshot is ingested. This module does not scrape the human consultation UI.
The legacy local heading table remains curated provisional fallback data.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from .models import Evidence, ProvenanceStatus

logger = logging.getLogger(__name__)

# EU geographic area codes (from TARIC consultation page)
# "1000" = European Union (all), "CN" selects China specifically
# The TARIC form uses numeric codes for geographic areas
EU_GEO_CODES = {
    "EU": "1000",       # European Union
    "CN": "1000",       # China — queries use country-specific codes in advanced mode
}

# Curated MFN duty reference — heading-level rates for high-demand HS codes
# Source: EU TARIC database (verified against TARIC consultation on 2024-2025)
# These are representative MFN (non-preferential) duty rates for the 4-digit HS heading.
# Actual subheading rates may differ — this is a known limitation.
CURATED_MFN_DUTY: dict[str, dict[str, Any]] = {
    "9617": {
        "heading": "Vacuum flasks and insulated containers",
        "mfn_duty_pct": 3.0,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 9617 (insulated containers, vacuum flasks). Representative heading-level rate; product-specific subheading rates may differ.",
    },
    "9619": {
        "heading": "Hot water bottles, bags, etc.",
        "mfn_duty_pct": 4.5,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 9619.",
    },
    "4202": {
        "heading": "Trunks, suitcases, bags, cases",
        "mfn_duty_pct": 2.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 4202 (luggage, bags, cases).",
    },
    "8507": {
        "heading": "Electric accumulators (rechargeable batteries)",
        "mfn_duty_pct": 2.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 8507 (electric accumulators, including lithium-ion batteries).",
    },
    "7307": {
        "heading": "Tubes and pipes of iron or steel",
        "mfn_duty_pct": 2.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 7307 (tubes and pipes of iron or steel).",
    },
    "8413": {
        "heading": "Pumps for liquids",
        "mfn_duty_pct": 1.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 8413 (pumps for liquids).",
    },
    "8479": {
        "heading": "Machines with individual functions",
        "mfn_duty_pct": 2.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 8479 (machines having individual functions).",
    },
    "8483": {
        "heading": "Transmission shafts, cranks, gears",
        "mfn_duty_pct": 2.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 8483 (transmission shafts, cranks, gears).",
    },
    "8544": {
        "heading": "Insulated wire, cable",
        "mfn_duty_pct": 2.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 8544 (insulated wire, cable).",
    },
    "8708": {
        "heading": "Parts and accessories for motor vehicles",
        "mfn_duty_pct": 2.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 8708 (motor vehicle parts).",
    },
    "9401": {
        "heading": "Seats (other than medical)",
        "mfn_duty_pct": 3.0,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 9401 (seats).",
    },
    "6203": {
        "heading": "Men's or boys' suits, jackets, trousers (woven)",
        "mfn_duty_pct": 12.0,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 6203 (men's woven clothing). Typical rate; product-specific subheading may differ.",
    },
    "6204": {
        "heading": "Women's or girls' suits, jackets, dresses (woven)",
        "mfn_duty_pct": 12.0,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 6204 (women's woven clothing).",
    },
    "6205": {
        "heading": "Men's or boys' shirts (woven)",
        "mfn_duty_pct": 12.0,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 6205 (men's shirts).",
    },
    "6206": {
        "heading": "Women's or girls' blouses, shirts (woven)",
        "mfn_duty_pct": 12.0,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 6206 (women's blouses).",
    },
    "6306": {
        "heading": "Cheesecloth, gauze, canvas, tarpaulins",
        "mfn_duty_pct": 12.0,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 6306 (tarpaulins, canvas).",
    },
    "8517": {
        "heading": "Telephone sets, smartphones, etc.",
        "mfn_duty_pct": 0.0,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 8517 (telephones, smartphones) — duty-free under EU ITA (Information Technology Agreement).",
    },
    "8528": {
        "heading": "Monitors, projectors",
        "mfn_duty_pct": 0.0,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 8528 (monitors, screens) — duty-free under EU ITA.",
    },
    "8471": {
        "heading": "Automatic data processing machines (computers)",
        "mfn_duty_pct": 0.0,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 8471 (computers, laptops) — duty-free under EU ITA.",
    },
    "8415": {
        "heading": "Refrigerating equipment, refrigerators, freezers",
        "mfn_duty_pct": 2.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 8415 (refrigerators, freezers).",
    },
    "8509": {
        "heading": "Electro-mechanical domestic appliances",
        "mfn_duty_pct": 2.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 8509 (electro-mechanical domestic appliances).",
    },
    "8516": {
        "heading": "Electric heating",
        "mfn_duty_pct": 2.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 8516 (electric heating appliances).",
    },
    "8536": {
        "heading": "Electrical switching apparatus, fuses",
        "mfn_duty_pct": 2.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 8536 (electrical switches, fuses).",
    },
    "8543": {
        "heading": "Electrical machines and apparatus",
        "mfn_duty_pct": 2.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 8543 (electrical machines).",
    },
    "8715": {
        "heading": "Baby transport equipment (strollers, etc.)",
        "mfn_duty_pct": 2.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 8715 (baby transport equipment).",
    },
    "9503": {
        "heading": "Tricycles, scooters, toy vehicles",
        "mfn_duty_pct": 12.0,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 9503 (toy vehicles, tricycles, scooters).",
    },
    "9504": {
        "heading": "Video game consoles",
        "mfn_duty_pct": 2.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 9504 (video game consoles).",
    },
    "9405": {
        "heading": "Lamps and lighting accessories",
        "mfn_duty_pct": 2.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 9405 (lamps, lighting).",
    },
    "8418": {
        "heading": "Refrigerators, freezers, cold stores",
        "mfn_duty_pct": 2.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 8418 (refrigeration equipment).",
    },
    "4016": {
        "heading": "Other articles of rubber",
        "mfn_duty_pct": 2.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 4016 (other rubber articles — e.g. bicycle tires, rubber components).",
    },
    "3926": {
        "heading": "Other articles of plastics",
        "mfn_duty_pct": 2.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 3926 (plastic kitchenware, plastic articles).",
    },
    "3212": {
        "heading": "Pigments and preparations with a basis of pigments",
        "mfn_duty_pct": 2.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 3212 (paints, pigments — cosmetics, paints).",
    },
    "3304": {
        "heading": "Beauty or make-up preparations, skincare",
        "mfn_duty_pct": 2.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 3304 (cosmetics, skincare, beauty preparations).",
    },
    "3006": {
        "heading": "Pharmaceutical goods, food supplements, etc.",
        "mfn_duty_pct": 2.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 3006 (pharmaceutical goods, food supplements, sterile surgical materials).",
    },
    "4419": {
        "heading": "Wooden articles, wooden tableware, kitchenware",
        "mfn_duty_pct": 2.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 4419 (wooden kitchenware, wooden articles).",
    },
    "8525": {
        "heading": "Transmission apparatus for radio/TV",
        "mfn_duty_pct": 2.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 8525 (transmission apparatus).",
    },
    "8529": {
        "heading": "Parts for TV, radio, etc.",
        "mfn_duty_pct": 2.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 8529 (parts for TV/radio equipment).",
    },
    "7326": {
        "heading": "Other articles of iron or steel",
        "mfn_duty_pct": 2.7,
        "effective_date": "2024",
        "taric_url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
        "note": "MFN duty for heading 7326 (other iron/steel articles — steel components, brackets, fittings).",
    },
}


class TaricAdapter:
    """Adapter for EU TARIC lookup.

    V0.1: Two-tier — live browser-based lookup (optional) + curated MFN reference.
    """

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout
        self._browser_available = False  # Will be set by the caller if browser is available

    def set_browser_available(self, available: bool) -> None:
        """Called by the service layer to indicate browser availability."""
        self._browser_available = available

    def get_mfn_duty(self, hs_code: str, destination_country: str = "FR") -> Optional[float]:
        """Return the MFN duty rate (%) for an HS heading in an EU destination.

        Priority:
          1. If browser is available and check_live_taric is True, attempt live lookup.
          2. Fall back to curated reference data.
          3. Return None if not available.
        """
        heading = hs_code[:4] if len(hs_code) >= 4 else hs_code

        if self._browser_available:
            try:
                result = self._live_lookup(heading, hs_code, destination_country)
                if result is not None:
                    return result
            except Exception as e:
                logger.debug("TARIC live lookup failed: %s", e)
                # Fall through to curated

        entry = CURATED_MFN_DUTY.get(heading)
        if entry:
            return entry["mfn_duty_pct"]
        return None

    def get_duty_evidence(
        self, hs_code: str, destination_country: str = "FR", live_used: bool = False
    ) -> Optional[Evidence]:
        """Return an Evidence record for the MFN duty rate, if available."""
        heading = hs_code[:4] if len(hs_code) >= 4 else hs_code
        entry = CURATED_MFN_DUTY.get(heading)
        if entry is None:
            return None

        # `live_used` historically only selected wording; the rate still came
        # from CURATED_MFN_DUTY. Never label that value as a live observation.
        status = ProvenanceStatus.ESTIMATED
        note = entry["note"] + " Curated heading-level reference; not a live tariff observation."
        authority = "FACTRAIL curated reference data"
        source = f"FACTRAIL curated EU MFN tariff reference (heading {heading})"
        url = entry["taric_url"]
        retrieved_at = datetime.now(timezone.utc)
        effective_date = entry["effective_date"]

        return Evidence(
            value=f"{entry['mfn_duty_pct']}%",
            status=status,
            authority=authority,
            source=source,
            url=url,
            retrieved_at=retrieved_at,
            effective_date=effective_date,
            supports=["duty", "classification"],
            confidence=1.0,
            note=note,
        )

    def _live_lookup(self, heading: str, full_code: str, destination: str) -> Optional[float]:
        """Live browser lookup is disabled; official evidence uses local snapshots."""
        raise NotImplementedError(
            "The TARIC human consultation UI is not scraped. Ingest an official "
            "Commission XLSX snapshot and query OfficialTaricStore instead."
        )


def lookup_taric_duty_via_browser(hs_code: str, origin_country: str, destination_country: str) -> Evidence:
    """Return a structured unavailable marker; no rendered-UI scraping occurs."""
    from ..classification import validate_hs_code

    if not validate_hs_code(hs_code):
        return Evidence(
            value=None,
            status=ProvenanceStatus.UNAVAILABLE,
            authority="European Commission — EU TARIC",
            source="EU TARIC (invalid goods code)",
            url="https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
            retrieved_at=datetime.now(timezone.utc),
            supports=["duty", "tariff"],
            confidence=0.0,
            note=f"Invalid HS/TARIC code format: {hs_code!r}",
        )

    return Evidence(
        value=None,
        status=ProvenanceStatus.UNAVAILABLE,
        authority="European Commission — EU TARIC",
        source="EU_TARIC live consultation lookup disabled",
        url="https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp?Lang=en",
        retrieved_at=datetime.now(timezone.utc),
        supports=["duty", "tariff"],
        confidence=0.0,
        source_outcome="lookup_disabled",
        source_detail="lookup_disabled",
        note="Live TARIC consultation UI lookup is intentionally disabled. Install an official Commission snapshot and query the local indexed store.",
    )
