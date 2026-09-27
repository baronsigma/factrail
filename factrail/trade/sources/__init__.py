"""V0.1 trade source stubs for EU authoritative data sources.

In V0 these are stubs that return "unavailable" with provenance.
The curated reference data path is in vat.py and customs.py.
Live integration with EU TARIC / Access2Markets / sanctions lists
is future work.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from ..models import Evidence, ProvenanceStatus


class TaricSource:
    """EU TARIC lookup (V0 stub — returns unavailable)."""

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout
        self._session: Optional[Any] = None  # future: httpx.AsyncClient

    def lookup_duty(
        self, hs_code: str, origin_country: str, destination_country: str
    ) -> Evidence:
        return Evidence(
            value=None,
            status=ProvenanceStatus.UNAVAILABLE,
            authority="European Commission — TARIC",
            source="EU TARIC (live lookup not available in V0)",
            url="https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
            retrieved_at=datetime.now(timezone.utc),
            supports=["duty", "tariff"],
            confidence=0.0,
            note="V0 does not integrate live TARIC queries — duty data not available in V0.",
        )


class Access2MarketsSource:
    """EU Access2Markets lookup (V0 stub)."""

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout
        self._session: Optional[Any] = None

    def lookup_market_access(
        self, origin_country: str, destination_country: str, hs_code: str
    ) -> Evidence:
        return Evidence(
            value=None,
            status=ProvenanceStatus.UNAVAILABLE,
            authority="European Commission — Access2Markets",
            source="EU Access2Markets (live lookup not available in V0)",
            url="https://trade.ec.europa.eu/access-to-markets",
            retrieved_at=datetime.now(timezone.utc),
            supports=["duty", "tariff", "preferential"],
            confidence=0.0,
            note="V0 does not integrate live Access2Markets queries.",
        )


class SanctionsSource:
    """EU sanctions / restrictive measures check (V0 stub)."""

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout
        self._session: Optional[Any] = None

    def check(
        self, origin_country: str, destination_country: str
    ) -> Evidence:
        return Evidence(
            value=None,
            status=ProvenanceStatus.UNAVAILABLE,
            authority="European Union — Consolidated Sanctions List",
            source="EU sanctions / restrictive measures (live check not available in V0)",
            url="https://data.europa.eu/data/datasets/consolidated-list-of-persons-entities-and-aircraft",
            retrieved_at=datetime.now(timezone.utc),
            supports=["sanctions"],
            confidence=0.0,
            note="V0 does not integrate live EU sanctions checks.",
        )


class EbtiSource:
    """Binding Tariff Information (BTI) lookup (V0 stub)."""

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout
        self._session: Optional[Any] = None

    def lookup_bti(
        self, country: str, hs_code: str
    ) -> Evidence:
        return Evidence(
            value=None,
            status=ProvenanceStatus.UNAVAILABLE,
            authority="National customs authority (BTI)",
            source="Binding Tariff Information (BTI) — live lookup not available in V0",
            url="https://ec.europa.eu/taxation_customs/union/bti",
            retrieved_at=datetime.now(timezone.utc),
            supports=["classification", "bti"],
            confidence=0.0,
            note="V0 does not integrate live BTI lookups — BTI is case-specific.",
        )


class VatSource:
    """EU / national VAT rate lookup (V0 — delegates to vat.py reference data)."""

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout
        self._session: Optional[Any] = None

    def lookup_vat_rate(self, destination_country: str) -> Evidence:
        from ..vat import eu_vat_rate

        rate = eu_vat_rate(destination_country)
        if rate is None:
            return Evidence(
                value=None,
                status=ProvenanceStatus.UNAVAILABLE,
                authority="National tax authority / European Commission",
                source="EU VAT rate (not available for this destination in V0)",
                url="https://ec.europa.eu/taxation_customs/vat_rates_en",
                retrieved_at=datetime.now(timezone.utc),
                supports=["vat_rate"],
                confidence=0.0,
            )
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
        return Evidence(
            value=f"{rate}%",
            status=ProvenanceStatus.VERIFIED,
            authority=f"National tax authority ({iso3}) / European Commission",
            source=f"EU VAT reference data — standard rate for {destination_country}",
            url="https://ec.europa.eu/taxation_customs/vat_rates_en",
            retrieved_at=datetime.now(timezone.utc),
            effective_date="2024",
            supports=["vat_rate"],
            confidence=1.0,
            note=f"Standard VAT rate for {destination_country} ({iso3}). "
                 "From published national reference; not scraped live in V0.",
        )
