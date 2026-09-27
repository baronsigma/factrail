"""Normalize BODACC records into Factrail's event taxonomy.

BODACC familleavis codes (official DILA mapping):
  creation                  → registration
  immatriculation           → registration
  modification              → modification
  radiation                 → removal
  vente                     → sale_or_transfer
  collective                → collective_proceeding
  conciliation              → conciliation
  retablissement_professionnel → professional_recovery
  dpc                       → accounts_filing
  divers                    → other

Strict event schema (V1.1):
  source_status: available | unavailable | disabled | partial
  source_error: null | timeout | http_4xx | http_5xx | rate_limited | malformed_response
  historical_removal_notices: [...] — historical BODACC removal notices;
    does NOT imply the company is currently inactive per INSEE.
  collective_proceedings: [...] — historical notices; presence does not
    imply a proceeding is currently active unless the source explicitly says so.
  possible_duplicate_group: non-authoritative grouping of notices that may
    be duplicates (same publication_date + category) — informational only.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

BODACC_FAMILLE_TO_CATEGORY = {
    "creation": "registration",
    "immatriculation": "registration",
    "modification": "modification",
    "radiation": "removal",
    "vente": "sale_or_transfer",
    "collective": "collective_proceeding",
    "conciliation": "conciliation",
    "retablissement_professionnel": "professional_recovery",
    "dpc": "accounts_filing",
    "divers": "other",
}


def _safe(d: dict, key: str) -> Optional[str]:
    v = d.get(key)
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return None
    return str(v).strip()


def _parse_json_field(raw: Any) -> Optional[dict]:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _extract_siren_from_registre(registre: Any) -> Optional[str]:
    """Extract the 9-digit SIREN from a registre field (multi-valued or string)."""
    if registre is None:
        return None
    if isinstance(registre, list):
        for item in registre:
            s = str(item).replace(" ", "")
            if len(s) == 9 and s.isdigit():
                return s
        if registre:
            s = str(registre[0]).replace(" ", "")
            if len(s) >= 9:
                return s[:9]
    return None


def normalize_notice(raw: dict, queried_siren: str) -> Optional[dict]:
    """Normalize a single BODACC record dict.

    Returns None if the record doesn't actually match the queried SIREN
    (defensive — the API uses LIKE matching).
    """
    registre = raw.get("registre")
    record_siren = _extract_siren_from_registre(registre)
    if record_siren is None or record_siren != queried_siren:
        return None

    famille_code = _safe(raw, "familleavis") or "divers"
    category = BODACC_FAMILLE_TO_CATEGORY.get(famille_code, "other")

    notice_id = _safe(raw, "id")
    date_parution = _safe(raw, "dateparution")
    url = _safe(raw, "url_complete")

    description_parts = []

    jugement = _safe(raw, "jugement")
    if jugement:
        description_parts.append(jugement)

    acte = _safe(raw, "acte")
    if acte:
        description_parts.append(acte)

    mods = _safe(raw, "modificationsgenerales")
    if mods:
        description_parts.append(mods)

    radiation = _safe(raw, "radiationaurcs")
    if radiation:
        description_parts.append(radiation)

    depot = _parse_json_field(raw.get("depot"))
    if depot:
        descriptif = depot.get("descriptif")
        if descriptif:
            description_parts.append(descriptif)

    divers = _safe(raw, "divers")
    if divers:
        description_parts.append(divers)

    description = " | ".join(description_parts) if description_parts else None

    return {
        "bodacc_notice_id": notice_id,
        "bodacc_famille_code": famille_code,
        "bodacc_famille_label": _safe(raw, "familleavis_lib"),
        "bodacc_typeavis": _safe(raw, "typeavis"),
        "bodacc_typeavis_label": _safe(raw, "typeavis_lib"),
        "category": category,
        "publication_date": date_parution,
        "notice_number": raw.get("numeroannonce"),
        "description": description,
        "tribunal": _safe(raw, "tribunal"),
        "department": _safe(raw, "departement_nom_officiel"),
        "source_url": url,
        "company_name": _safe(raw, "commercant"),
    }


def _build_event(normalized: dict, retrieved_at: datetime) -> dict:
    """Build a clean event dict from a normalized notice."""
    return {
        "category": normalized["category"],
        "bodacc_typeavis": normalized["bodacc_typeavis"],
        "bodacc_typeavis_label": normalized["bodacc_typeavis_label"],
        "bodacc_famille_code": normalized["bodacc_famille_code"],
        "bodacc_famille_label": normalized["bodacc_famille_label"],
        "publication_date": normalized["publication_date"],
        "notice_number": normalized["notice_number"],
        "bodacc_notice_id": normalized["bodacc_notice_id"],
        "description": normalized["description"],
        "source_url": normalized["source_url"],
        "company_name": normalized["company_name"],
        "tribunal": normalized["tribunal"],
        "retrieved_at": retrieved_at.isoformat() if retrieved_at else None,
    }


def _assign_duplicate_groups(events: list[dict]) -> None:
    """Assign a non-authoritative possible_duplicate_group to events.

    Two notices with the same publication_date AND category are flagged
    as possible duplicates. This does NOT remove any notices.
    """
    groups: dict[tuple, str] = {}
    for event in events:
        pub = event.get("publication_date") or "none"
        cat = event.get("category") or "none"
        key = (pub, cat)
        if key not in groups:
            groups[key] = f"group_{pub}_{cat}"
        event["possible_duplicate_group"] = groups[key]


def build_events_section(
    records: list[dict],
    queried_siren: str,
    retrieved_at: datetime,
    truncated: bool,
    total: int,
) -> dict:
    """Build the events section from a list of BODACC records.

    - Preserves ALL notices (no deduplication)
    - Adds possible_duplicate_group for notices with same date + category
    - Sorts by publication_date desc
    - Collects historical removal and collective-proceeding notices
    """
    events: list[dict] = []
    historical_removal_notices: list[dict] = []
    collective_proceedings: list[dict] = []
    latest_accounts_filing: Optional[dict] = None
    latest_event_at: Optional[str] = None

    for raw in records:
        normalized = normalize_notice(raw, queried_siren)
        if normalized is None:
            continue

        event = _build_event(normalized, retrieved_at)

        pub_date = normalized["publication_date"]
        if pub_date:
            if latest_event_at is None or pub_date > latest_event_at:
                latest_event_at = pub_date

        if normalized["category"] == "removal":
            historical_removal_notices.append(event)

        if normalized["category"] == "collective_proceeding":
            collective_proceedings.append(event)

        if normalized["category"] == "accounts_filing":
            if latest_accounts_filing is None or (
                pub_date
                and latest_accounts_filing.get("publication_date")
                and pub_date > latest_accounts_filing["publication_date"]
            ):
                latest_accounts_filing = event

        events.append(event)

    # Sort by publication_date desc (None last)
    events.sort(
        key=lambda e: e.get("publication_date") or "0000-00-00",
        reverse=True,
    )

    # Assign non-authoritative duplicate groups
    _assign_duplicate_groups(events)

    return {
        "latest": events[:10],
        "latest_event_at": latest_event_at,
        "historical_removal_notices": historical_removal_notices,
        "collective_proceedings": collective_proceedings,
        "latest_accounts_filing": latest_accounts_filing,
        "source_status": "available",
        "source_error": None,
        "total_notices": total,
        "returned_notices": len(events),
        "truncated": truncated,
    }


def build_unavailable_events(retrieved_at: datetime, error: str) -> dict:
    """Build an events section when BODACC is unavailable."""
    return {
        "latest": [],
        "latest_event_at": None,
        "historical_removal_notices": [],
        "collective_proceedings": [],
        "latest_accounts_filing": None,
        "source_status": "unavailable",
        "source_error": error,
        "total_notices": None,
        "returned_notices": 0,
        "truncated": False,
    }


def build_disabled_events(retrieved_at: datetime) -> dict:
    """Build an events section when BODACC is disabled."""
    return {
        "latest": [],
        "latest_event_at": None,
        "historical_removal_notices": [],
        "collective_proceedings": [],
        "latest_accounts_filing": None,
        "source_status": "disabled",
        "source_error": None,
        "total_notices": None,
        "returned_notices": 0,
        "truncated": False,
    }


def build_not_found_events(retrieved_at: datetime) -> dict:
    """Build an events section when no BODACC notices exist for the SIREN."""
    return {
        "latest": [],
        "latest_event_at": None,
        "historical_removal_notices": [],
        "collective_proceedings": [],
        "latest_accounts_filing": None,
        "source_status": "available",
        "source_error": None,
        "total_notices": 0,
        "returned_notices": 0,
        "truncated": False,
    }
