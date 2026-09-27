#!/usr/bin/env python3
"""
Seed the TARIC cache with authoritative data retrieved from the live
EU Access2Markets site.  The data was obtained by rendering the A2M Angular
SPA with a browser tool (web_extract) and extracting the tariff measures
from the rendered HTML.

This seeds the local SQLite cache so that offline/test queries get the
authoritative 6.70% MFN duty for 9617 CN→FR instead of the old curated
3.0% fallback.

Data provenance:
  - Source: EU Access2Markets, version 2026.7.2.1 (2026-09-25 13:15)
  - URL: https://trade.ec.europa.eu/access-to-markets/en/results?product=961700&origin=CN&destination=FR
  - Tariff data source: DG TAXUD TARIC database (updated daily)
  - Third-country duty: 6.70% under ERGA OMNES, EU law R2261/98
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from factrail.trade.taric_store import TaricStore

# Parsed measures from the live A2M page (web_extract rendered)
A2M_SEED_DATA = {
    "version": "2026.7.2.1",
    "version_date": "2026-09-25",
    "measures": [
        {
            "origin_area": "ERGA OMNES",
            "measure_type": "Third country duty",
            "measure_kind": "third_country_duty",
            "tariff": "6.70%",
            "eu_law_reference": "R2261/98",
            "footnotes": [],
            "conditions": [],
            "review_required": False,
        },
        {
            "origin_area": "ERGA OMNES",
            "measure_type": "Low-value consignment customs duty",
            "measure_kind": "low_value_consignment_duty",
            "tariff": "3.00 EUR",
            "eu_law_reference": "R0382/26",
            "footnotes": [
                {
                    "code": "TM1066",
                    "text": (
                        "From 1 July 2026 until 1 July 2028, a customs duty of EUR 3 per item "
                        "in a consignment the intrinsic value of which does not exceed a total of "
                        "EUR 150 shall apply instead of the relief eliminated pursuant to Article 1 "
                        "of this Regulation, where: (a) the importation of the goods is exempt from "
                        "VAT in accordance with Article 143(1), point (ca), of Directive 2006/112/EC; "
                        "or (b) the goods are in a postal consignment as defined in Article 1, point (24), "
                        "of Delegated Regulation (EU) 2015/2446."
                    ),
                }
            ],
            "conditions": [],
            "review_required": True,
        },
        {
            "origin_area": "ERGA OMNES",
            "measure_type": "Suspension - goods for certain categories of ships, boats and other vessels and for drilling or production platforms",
            "measure_kind": "tariff_suspension",
            "tariff": "0%",
            "eu_law_reference": "R2658/87",
            "footnotes": [
                {
                    "code": "TM510",
                    "text": (
                        "1. Customs duties shall be suspended in respect of goods intended for incorporation "
                        "in the ships, boats or other vessels classified at the following CN codes 8901 10 10; "
                        "8901 20 10; 8901 30 10; 8901 90 10; 8902 00 10; 8903 22 10, 8903 23 10, 8903 32 10, "
                        "8903 33 10; 8904 00 10; 8904 00 91; 8905 10 10; 8905 90 10; 8906 10 00; 8906 90 10 "
                        "for the purposes of their construction, repair, maintenance or conversion, and in respect "
                        "of goods intended for fitting to or equipping such ships, boats or other vessels. "
                        "2. Customs duties shall be suspended in respect of: (a) goods intended for incorporation "
                        "in drilling or production platforms: (1) fixed, of subheading ex 8430 49, operating in "
                        "or outside the territorial sea of Member States, or (2) floating or submersible, of "
                        "subheading 8905 20, for the purposes of their construction, repair, maintenance or "
                        "conversion, and in respect of goods intended for equipping the said platforms. "
                        "(b) tubes, pipes, cables and their connection pieces, linking these drilling or production "
                        "platforms to the mainland."
                    ),
                },
                {
                    "code": "EU003",
                    "text": (
                        "According to The Special Provisions of Section II (A) (3) of the Preliminary Provisions "
                        "of the Combined Nomenclature the suspension of customs duties for goods for certain "
                        "categories of ships, boats and other vessels and for drilling or production platforms "
                        "shall be subject to conditions laid down in the relevant provisions of the European Union "
                        "with a view to customs control of the use of such goods."
                    ),
                },
            ],
            "conditions": [
                {
                    "code": "B1",
                    "certificate_or_document": "C990: End use authorisation ships and platforms (Column 8c, Annex A of Delegated Regulation (EU) 2015/2446)",
                    "action": "Apply the mentioned duty",
                    "note": "",
                },
                {
                    "code": "B2",
                    "certificate_or_document": "Presentation of a certificate/licence/document",
                    "action": "Measure not applicable",
                    "note": "",
                },
            ],
            "review_required": True,
        },
        {
            "origin_area": "ERGA OMNES",
            "measure_type": "Airworthiness tariff suspension",
            "measure_kind": "airworthiness_suspension",
            "tariff": "0%",
            "eu_law_reference": "R1517/18",
            "footnotes": [
                {
                    "code": "CD333",
                    "text": (
                        "The autonomous Common Customs Tariff duties laid down in Regulation (EEC) No 2658/87 "
                        "for parts, components and other goods of a kind to be incorporated in or used for aircraft "
                        "and parts thereof in the course of their manufacture, repair, maintenance, rebuilding, "
                        "modification or conversion is suspended. In order to benefit from the suspension, the "
                        "declarant shall present to the customs authorities an Authorised Release Certificate — "
                        "EASA Form 1, as set out in Appendix I to Annex I to Regulation (EU) No 748/2012, or an "
                        "equivalent certificate. The certificates which are deemed to be equivalent to Authorised "
                        "Release Certificates are listed in Annex II to the Regulation (EU) 2018/1517."
                    ),
                }
            ],
            "conditions": [
                {
                    "code": "C1",
                    "certificate_or_document": "C119: Authorised Release Certificate — EASA Form 1 (Appendix I to Annex I to Regulation (EU) No 748/2012), or equivalent certificate",
                    "action": "Apply the mentioned duty",
                    "note": "",
                },
                {
                    "code": "C2",
                    "certificate_or_document": "Presentation of a certificate/licence/document",
                    "action": "Measure not applicable",
                    "note": "",
                },
            ],
            "review_required": True,
        },
    ],
    "source_note": (
        "EU Access2Markets (v2026.7.2.1, 2026-09-25) — tariff data from "
        "DG TAXUD TARIC database (EU public sector information) and Mendel Verlag "
        "(non-EU third-country duties; personal reference use). "
        "URL: https://trade.ec.europa.eu/access-to-markets/en/results?product=961700&origin=CN&destination=FR"
    ),
}


def seed_cache(
    hs_code: str = "961700",
    origin: str = "CN",
    destination: str = "FR",
    store: TaricStore | None = None,
) -> bool:
    """Seed the TARIC cache with authoritative A2M data for a trade flow.

    Returns True if the seed was written, False if the cache already had
    fresher data.
    """
    store = store or TaricStore()

    existing = store.lookup(hs_code, origin, destination)
    if existing and not existing.get("cached"):
        # Live data already cached and fresh — don't overwrite
        print(f"Cache already has fresh live data for {hs_code} {origin}→{destination}")
        return False

    store.store(hs_code, origin, destination, A2M_SEED_DATA)
    print(f"Seeded TARIC cache for {hs_code} {origin}→{destination}")
    print(f"  Measures: {len(A2M_SEED_DATA['measures'])}")
    for m in A2M_SEED_DATA["measures"]:
        print(f"    - {m['measure_type']}: {m['tariff']} ({m['measure_kind']})")
    return True


if __name__ == "__main__":
    seed_cache()
