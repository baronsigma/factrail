"""Normalization: INSEE Sirene API → Factrail FrenchCompany.

Null semantics: every field records WHY it is null (unavailable | unknown |
not_applicable) via a parallel <field>_reason key in the output JSON.

Source provenance: each SourceMeta carries the exact timestamp at which that
source was queried. ``checked_at`` is the timestamp of the LAST source lookup.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from .models import Address, FrenchCompany, SourceMeta, mark_null

INSEE_API_BASE = "https://api.insee.fr/api-sirene/3.11"
INSEE_API_NAME = "INSEE Sirene 3.11"

EMPLOYEE_BANDS = {
    "NN": "non employeur",
    "00": "0 salarié",
    "01": "1 ou 2 salariés",
    "02": "3 à 5 salariés",
    "03": "6 à 9 salariés",
    "11": "10 à 19 salariés",
    "12": "20 à 49 salariés",
    "21": "50 à 99 salariés",
    "22": "100 à 199 salariés",
    "31": "200 à 249 salariés",
    "32": "250 à 499 salariés",
    "41": "500 à 999 salariés",
    "42": "1 000 à 1 999 salariés",
    "51": "2 000 à 4 999 salariés",
    "52": "5 000 à 9 999 salariés",
    "53": "10 000 salariés et plus",
    "unknown": "unknown",
}


def _status(etat: Any) -> Optional[str]:
    if etat == "A":
        return "active"
    if etat == "C":
        return "inactive"
    return None


def _diffusion_status(code: Any) -> Optional[str]:
    if code == "O":
        return "diffusible"
    if code == "P":
        return "diffusion_partielle"
    return None


def _addr(d: dict) -> Optional[Address]:
    parts = [
        _safe_str(d, "numeroVoieEtablissement"),
        _safe_str(d, "typeVoieEtablissement"),
        _safe_str(d, "libelleVoieEtablissement"),
        _safe_str(d, "complementAdresseEtablissement"),
    ]
    line_1 = " ".join(p for p in parts if p)
    if (
        not line_1
        and not _safe_str(d, "libelleCommuneEtablissement")
        and not _safe_str(d, "codePostalEtablissement")
    ):
        return None
    return Address(
        line_1=line_1 or None,
        locality=_safe_str(d, "libelleCommuneEtablissement"),
        postal_code=_safe_str(d, "codePostalEtablissement"),
        country="FR",
    )


def _safe_str(d: dict, key: str) -> Optional[str]:
    v = d.get(key)
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _employee_band(code: Any) -> Optional[str]:
    if code is None:
        return None
    return EMPLOYEE_BANDS.get(code)


def _int_or_none(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _mark_null_for_period(
    fc: FrenchCompany, raw_period: dict, field_name: str, key: str
) -> None:
    """Emit a null reason for a field extracted from a period dict.

    If the key is present in the period → "unknown" (INSEE returned null).
    If the key is absent → "unavailable" (field not in this period).
    """
    if getattr(fc, field_name) is None:
        mark_null(fc, field_name, "unknown" if key in raw_period else "unavailable")


def _mark_null_for_dict(
    fc: FrenchCompany, raw: dict, field_name: str, key: str
) -> None:
    """Emit a null reason for a field extracted from a flat dict."""
    if getattr(fc, field_name) is None:
        mark_null(fc, field_name, "unknown" if key in raw else "unavailable")


def normalize_siren_response(
    raw: dict,
    retrieved_at: Optional[datetime] = None,
    siege_raw: Optional[dict] = None,
    siege_retrieved_at: Optional[datetime] = None,
) -> FrenchCompany:
    """Normalize a GET /siren/{siren} response.

    The SIREN endpoint returns only legal-unit data; it does NOT include an
    address.  If ``siege_raw`` is provided (from a second
    GET /siret/{siren}{nicSiegeUniteLegale} call), we use it for the
    address field.
    """
    ul = raw.get("uniteLegale", raw)
    periodes = ul.get("periodesUniteLegale", []) or []
    latest = periodes[0] if periodes else {}
    siren_val = _safe_str(ul, "siren") or ""

    fc = FrenchCompany(
        siren=siren_val,
        legal_name=_safe_str(latest, "denominationUniteLegale"),
        trade_name=_safe_str(latest, "nomUniteLegale"),
        status=_status(latest.get("etatAdministratifUniteLegale")),
        diffusion_status=_diffusion_status(ul.get("statutDiffusionUniteLegale")),
        legal_form_code=_safe_str(ul, "categorieJuridiqueUniteLegale"),
        creation_date=_safe_str(ul, "dateCreationUniteLegale"),
        cessation_date=_safe_str(latest, "dateFin"),
        naf_code=_safe_str(latest, "activitePrincipaleUniteLegale"),
        naf_code_nomenclature=_safe_str(
            latest, "nomenclatureActivitePrincipaleUniteLegale"
        ),
        employee_band=_employee_band(ul.get("trancheEffectifsUniteLegale")),
        employee_band_year=_int_or_none(ul.get("anneeEffectifsUniteLegale")),
        sources=[],
        checked_at=datetime.now(timezone.utc),
    )

    # Null reasons for fields that are present in the response but null
    _mark_null_for_period(fc, latest, "legal_name", "denominationUniteLegale")
    _mark_null_for_period(fc, latest, "trade_name", "nomUniteLegale")
    _mark_null_for_period(fc, latest, "naf_code", "activitePrincipaleUniteLegale")
    _mark_null_for_period(
        fc, latest, "naf_code_nomenclature", "nomenclatureActivitePrincipaleUniteLegale"
    )
    _mark_null_for_dict(fc, ul, "employee_band", "trancheEffectifsUniteLegale")
    _mark_null_for_dict(fc, ul, "employee_band_year", "anneeEffectifsUniteLegale")

    # Sources
    fc.sources.append(
        SourceMeta(
            id="insee-siren",
            name=INSEE_API_NAME,
            url=f"{INSEE_API_BASE}/siren/{siren_val}",
            retrieved_at=retrieved_at or datetime.now(timezone.utc),
        )
    )

    # Address from the siege SIRET call
    if siege_raw:
        etab = siege_raw.get("etablissement", siege_raw)
        fc.address = _addr(etab.get("adresseEtablissement", {}))
        siege_siret = _safe_str(etab, "siret") or ""
        fc.siret = siege_siret or None
        fc.sources.append(
            SourceMeta(
                id="insee-siret",
                name=INSEE_API_NAME,
                url=f"{INSEE_API_BASE}/siret/{siege_siret}",
                retrieved_at=siege_retrieved_at or datetime.now(timezone.utc),
            )
        )
    else:
        mark_null(fc, "address", "unavailable")

    fc.checked_at = datetime.now(timezone.utc)
    return fc


def normalize_siret_response(
    raw: dict, retrieved_at: Optional[datetime] = None
) -> FrenchCompany:
    """Normalize a GET /siret/{siret} response."""
    etab = raw.get("etablissement", raw)
    ul = etab.get("uniteLegale", {})
    periodes = etab.get("periodesEtablissement", []) or []
    latest_period = periodes[0] if periodes else {}

    fc = FrenchCompany(
        siren=_safe_str(etab, "siren") or _safe_str(ul, "siren") or "",
        siret=_safe_str(etab, "siret") or "",
        legal_name=_safe_str(latest_period, "denominationUniteLegale")
        or _safe_str(ul, "denominationUniteLegale"),
        trade_name=_safe_str(latest_period, "nomUniteLegale")
        or _safe_str(ul, "nomUniteLegale"),
        status=_status(latest_period.get("etatAdministratifEtablissement"))
        or _status(latest_period.get("etatAdministratifUniteLegale"))
        or _status(ul.get("etatAdministratifUniteLegale")),
        diffusion_status=_diffusion_status(etab.get("statutDiffusionEtablissement")),
        legal_form_code=_safe_str(ul, "categorieJuridiqueUniteLegale"),
        creation_date=_safe_str(ul, "dateCreationUniteLegale"),
        cessation_date=_safe_str(latest_period, "dateFin"),
        naf_code=_safe_str(etab, "activitePrincipaleEtablissement")
        or _safe_str(latest_period, "activitePrincipaleUniteLegale")
        or _safe_str(ul, "activitePrincipaleUniteLegale"),
        naf_code_nomenclature=_safe_str(
            etab, "nomenclatureActivitePrincipaleEtablissement"
        )
        or _safe_str(latest_period, "nomenclatureActivitePrincipaleUniteLegale")
        or _safe_str(ul, "nomenclatureActivitePrincipaleUniteLegale"),
        employee_band=_employee_band(
            etab.get("trancheEffectifsEtablissement")
            or latest_period.get("trancheEffectifsEtablissement")
            or ul.get("trancheEffectifsUniteLegale")
        ),
        employee_band_year=_int_or_none(
            etab.get("anneeEffectifsEtablissement")
            or latest_period.get("anneeEffectifsEtablissement")
            or ul.get("anneeEffectifsUniteLegale")
        ),
        address=_addr(etab.get("adresseEtablissement", {})),
        sources=[
            SourceMeta(
                id="insee-siret",
                name=INSEE_API_NAME,
                url=f"{INSEE_API_BASE}/siret/{_safe_str(etab, 'siret')}",
                retrieved_at=retrieved_at or datetime.now(timezone.utc),
            )
        ],
        checked_at=datetime.now(timezone.utc),
    )

    if fc.address is None:
        mark_null(fc, "address", "unknown")

    return fc
