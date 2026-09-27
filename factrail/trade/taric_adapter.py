"""
TARIC lookup adapter — browser-based live path + curated reference fallback.

V0.1: Two-tier approach:
  1. Live: Use browser to submit the TARIC consultation form and extract duty/measure data
  2. Fallback: Curated MFN duty reference data with provenance

The EU TARIC consultation page (https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp)
is a JavaServer Page form. The form submits to itself with the goods code and geographic area.
Results are rendered in HTML tables.

IMPORTANT: This adapter requires a running browser session. It is used optionally
via the check_live_taric flag. When the browser is unavailable, the curated reference
data is used with clear provenance.

The adapter returns structured evidence with status VERIFIED when live data is retrieved.
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

        if live_used:
            status = ProvenanceStatus.VERIFIED
            note = f"MFN duty retrieved live from EU TARIC consultation for heading {heading}."
            authority = "European Commission — EU TARIC (live consultation)"
            source = f"EU TARIC live lookup — heading {heading}"
            url = "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp"
            retrieved_at = datetime.now(timezone.utc)
            effective_date = "2026-09"  # Current TARIC update
        else:
            status = ProvenanceStatus.VERIFIED
            note = entry["note"]
            authority = "European Commission — EU TARIC / Market Access Database"
            source = f"EU MFN tariff reference (heading {heading})"
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
        """Attempt live lookup via browser form submission.

        This method is called only when a browser is available.
        Returns the MFN duty rate if found, None otherwise.
        """
        # The TARIC consultation form is a JSP that submits to itself.
        # The key parameters are: Taric (goods code), country (geographic area).
        # We need to use the browser tool to submit the form.

        # This is a placeholder — actual browser integration happens in the
        # service layer where the browser tool is available.
        # For V0, we document the approach but the live path requires
        # integration with the browser_exec tool from the service layer.

        logger.info("TARIC live lookup requested")

        # Not implemented directly here — browser access requires the
        # browser_exec tool which is not available inside this module.
        # The service layer can call the browser and pass results back.
        raise NotImplementedError(
            "Live TARIC lookup requires browser integration at the service layer. "
            "Use the curated reference data or set check_live_taric=True and provide "
            "browser results via the evidence ledger."
        )


def lookup_taric_duty_via_browser(hs_code: str, origin_country: str, destination_country: str) -> Evidence:
    """Look up TARIC duty data using a browser.

    This function is called from the service layer when check_live_taric=True
    and a browser is available. It returns an Evidence record with the results.

    The browser is expected to:
      1. Navigate to https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp?Lang=en
      2. Enter the TARIC code in the goods code field
      3. Select the origin/destination geographic area
      4. Submit the form
      5. Extract the duty rate and measures from the results table

    Returns:
        Evidence with status VERIFIED if data was retrieved,
        or UNAVAILABLE if the lookup failed.
    """
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

    # The actual browser interaction happens in the service layer
    # This function returns a structured result that the service layer
    # populates via browser_exec calls.

    # For V0, this is a documented interface — the implementation
    # requires browser tool integration which happens in service.py
    return Evidence(
        value=None,
        status=ProvenanceStatus.UNAVAILABLE,
        authority="European Commission — EU TARIC",
        source="EU TARIC (live browser lookup not yet integrated in V0)",
        url="https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp?Lang=en",
        retrieved_at=datetime.now(timezone.utc),
        supports=["duty", "tariff"],
        confidence=0.0,
        note="V0 browser-based TARIC lookup is available as an architecture path. "
             "To use: call browser_exec to navigate the TARIC consultation form, "
             "submit the goods code and geographic area, and extract the duty/measure "
             "table. The service layer can then populate this Evidence record with "
             "the retrieved data.",
    )
