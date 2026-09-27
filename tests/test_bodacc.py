"""Tests for BODACC adapter and normalization."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
import respx

from factrail.models import FrenchCompany, mark_null
from factrail.sources.bodacc import BodaccAdapter, BodaccUnavailableError
from factrail.sources.insee import InseeAdapter, NotFoundError, ERROR_TIMEOUT, ERROR_HTTP_5XX
from factrail.normalize_bodacc import (
    normalize_notice,
    build_events_section,
    build_unavailable_events,
    build_disabled_events,
    build_not_found_events,
)


def make_notice(**overrides):
    base = {
        "id": "C202500182618",
        "publicationavis": "C",
        "parution": "20250018",
        "dateparution": "2025-01-26",
        "numeroannonce": 2618,
        "typeavis": "annonce",
        "typeavis_lib": "Avis initial",
        "familleavis": "dpc",
        "familleavis_lib": "Dépôts des comptes",
        "numerodepartement": "60",
        "departement_nom_officiel": "Oise",
        "region_code": 32,
        "region_nom_officiel": "Hauts-de-France",
        "tribunal": "Greffe du Tribunal de Commerce de compiègne",
        "commercant": "Le Fournil des Bocages",
        "ville": "Thiescourt",
        "registre": ["752461681", "752 461 681"],
        "cp": "60310",
        "pdf_parution_subfolder": 1,
        "ispdf_unitaire": "oui",
        "listepersonnes": '{"personne": {"typePersonne": "pm"}}',
        "listeetablissements": None,
        "jugement": None,
        "acte": None,
        "modificationsgenerales": None,
        "radiationaurcs": None,
        "depot": '{"dateCloture": "2024-08-31", "typeDepot": "Comptes annuels"}',
        "listeprecedentexploitant": None,
        "listeprecedentproprietaire": None,
        "divers": None,
        "parutionavisprecedent": None,
        "url_complete": "https://www.bodacc.fr/pages/annonces-commerciales-detail/?q.id=id:C202500182618",
    }
    base.update(overrides)
    return base


class TestBodaccAdapter:
    @pytest.fixture
    def adapter(self):
        return BodaccAdapter(
            base_url="https://bodacc-datadila.opendatasoft.com/api/explore/v2.0",
            dataset="annonces-commerciales",
        )

    @respx.mock
    def test_search_no_results(self, adapter):
        url = "https://bodacc-datadila.opendatasoft.com/api/explore/v2.0/catalog/datasets/annonces-commerciales/records"
        respx.get(url).mock(
            return_value=__import__("httpx").Response(
                200, json={"total_count": 0, "records": []}
            )
        )
        result = adapter.search_by_siren("000000000")
        assert result["total"] == 0
        assert result["records"] == []

    @respx.mock
    def test_search_with_results(self, adapter):
        url = "https://bodacc-datadila.opendatasoft.com/api/explore/v2.0/catalog/datasets/annonces-commerciales/records"
        respx.get(url).mock(
            return_value=__import__("httpx").Response(
                200,
                json={
                    "total_count": 2,
                    "records": [
                        {"record": {"fields": make_notice()}},
                        {"record": {"fields": make_notice(id="C202500182619", numeroannonce=2619)}},
                    ],
                },
            )
        )
        result = adapter.search_by_siren("752461681")
        assert result["total"] == 2
        assert len(result["records"]) == 2

    @respx.mock
    def test_search_truncated(self, adapter):
        url = "https://bodacc-datadila.opendatasoft.com/api/explore/v2.0/catalog/datasets/annonces-commerciales/records"
        respx.get(url).mock(
            return_value=__import__("httpx").Response(
                200,
                json={
                    "total_count": 100,
                    "records": [{"record": {"fields": make_notice()}} for _ in range(10)],
                },
            )
        )
        result = adapter.search_by_siren("752461681", limit=10)
        assert result["truncated"] is True

    @respx.mock
    def test_search_timeout(self, adapter):
        url = "https://bodacc-datadila.opendatasoft.com/api/explore/v2.0/catalog/datasets/annonces-commerciales/records"
        respx.get(url).mock(
            side_effect=__import__("httpx").TimeoutException("timeout")
        )
        with pytest.raises(BodaccUnavailableError):
            adapter.search_by_siren("752461681")

    @respx.mock
    def test_search_5xx(self, adapter):
        url = "https://bodacc-datadila.opendatasoft.com/api/explore/v2.0/catalog/datasets/annonces-commerciales/records"
        respx.get(url).mock(
            return_value=__import__("httpx").Response(500, text="error")
        )
        with pytest.raises(BodaccUnavailableError):
            adapter.search_by_siren("752461681")


class TestNormalizeNotice:
    def test_basic_modification(self):
        n = make_notice(familleavis="modification", familleavis_lib="Modifications diverses")
        result = normalize_notice(n, "752461681")
        assert result is not None
        assert result["category"] == "modification"

    def test_accounts_filing(self):
        n = make_notice(familleavis="dpc", familleavis_lib="Dépôts des comptes")
        result = normalize_notice(n, "752461681")
        assert result["category"] == "accounts_filing"
        assert result["publication_date"] == "2025-01-26"

    def test_removal(self):
        n = make_notice(familleavis="radiation", familleavis_lib="Radiations")
        result = normalize_notice(n, "752461681")
        assert result["category"] == "removal"

    def test_sale(self):
        n = make_notice(familleavis="vente", familleavis_lib="Ventes et cessions")
        result = normalize_notice(n, "752461681")
        assert result["category"] == "sale_or_transfer"

    def test_collective_proceeding(self):
        n = make_notice(
            familleavis="collective",
            familleavis_lib="Procédures collectives",
            jugement="Redressement judiciaire",
        )
        result = normalize_notice(n, "752461681")
        assert result["category"] == "collective_proceeding"
        assert "Redressement" in result["description"]

    def test_immatriculation(self):
        n = make_notice(familleavis="immatriculation", familleavis_lib="Immatriculations")
        result = normalize_notice(n, "752461681")
        assert result["category"] == "registration"

    def test_unknown_famille_falls_back_to_other(self):
        n = make_notice(familleavis="weird_unknown_code")
        result = normalize_notice(n, "752461681")
        assert result["category"] == "other"

    def test_siren_mismatch_returns_none(self):
        n = make_notice(registre=["999999999", "999 999 999"])
        result = normalize_notice(n, "752461681")
        assert result is None


class TestBuildEventsSection:
    def test_empty_records(self):
        section = build_events_section([], "752461681", datetime.now(), False, 0)
        assert section["latest"] == []
        assert section["source_status"] == "available"
        assert section["source_error"] is None
        assert section["historical_removal_notices"] == []

    def test_single_modification(self):
        records = [make_notice(familleavis="modification")]
        section = build_events_section(records, "752461681", datetime.now(), False, 1)
        assert len(section["latest"]) == 1
        assert section["latest"][0]["category"] == "modification"

    def test_multiple_event_categories(self):
        records = [
            make_notice(id="C202400100001", familleavis="creation", dateparution="2024-01-01"),
            make_notice(id="C202400100002", familleavis="modification", dateparution="2024-06-01"),
            make_notice(id="C202400100003", familleavis="dpc", dateparution="2025-01-01"),
        ]
        section = build_events_section(records, "752461681", datetime.now(), False, 3)
        assert len(section["latest"]) == 3
        assert section["latest"][0]["category"] == "accounts_filing"
        assert section["latest_event_at"] == "2025-01-01"

    def test_historical_removal_notices(self):
        records = [make_notice(familleavis="radiation")]
        section = build_events_section(records, "752461681", datetime.now(), False, 1)
        assert len(section["historical_removal_notices"]) == 1

    def test_collective_proceeding(self):
        records = [make_notice(familleavis="collective", jugement="Liquidation judiciaire")]
        section = build_events_section(records, "752461681", datetime.now(), False, 1)
        assert len(section["collective_proceedings"]) == 1

    def test_latest_accounts_filing(self):
        records = [
            make_notice(id="C202400100001", familleavis="dpc", dateparution="2023-01-01"),
            make_notice(id="C202400100002", familleavis="dpc", dateparution="2025-01-01"),
            make_notice(id="C202400100003", familleavis="dpc", dateparution="2024-01-01"),
        ]
        section = build_events_section(records, "752461681", datetime.now(), False, 3)
        assert section["latest_accounts_filing"]["publication_date"] == "2025-01-01"

    def test_no_deduplication(self):
        """V1.1: separate official notice IDs are NOT deduplicated."""
        records = [
            make_notice(id="C202500182618"),
            make_notice(id="C202500182619"),
        ]
        section = build_events_section(records, "752461681", datetime.now(), False, 2)
        assert len(section["latest"]) == 2

    def test_possible_duplicate_group(self):
        """V1.1: same date + category gets a non-authoritative group."""
        records = [
            make_notice(id="C202500182618", dateparution="2025-01-26", familleavis="dpc"),
            make_notice(id="C202500182619", dateparution="2025-01-26", familleavis="dpc"),
        ]
        section = build_events_section(records, "752461681", datetime.now(), False, 2)
        assert "possible_duplicate_group" in section["latest"][0]
        assert section["latest"][0]["possible_duplicate_group"] == section["latest"][1]["possible_duplicate_group"]

    def test_truncated_reported(self):
        records = [make_notice()]
        section = build_events_section(records, "752461681", datetime.now(), True, 100)
        assert section["truncated"] is True
        assert section["total_notices"] == 100

    def test_returned_notices_count(self):
        records = [make_notice(), make_notice(id="C202500182619")]
        section = build_events_section(records, "752461681", datetime.now(), False, 2)
        assert section["returned_notices"] == 2

    def test_personal_info_excluded(self):
        records = [make_notice()]
        section = build_events_section(records, "752461681", datetime.now(), False, 1)
        event = section["latest"][0]
        assert "listepersonnes" not in event
        assert "personne" not in (event.get("description") or "")


class TestBuildUnavailableEvents:
    def test_unavailable_structure(self):
        section = build_unavailable_events(datetime.now(), error=ERROR_TIMEOUT)
        assert section["latest"] == []
        assert section["source_status"] == "unavailable"
        assert section["source_error"] == ERROR_TIMEOUT
        assert section["historical_removal_notices"] == []


class TestBuildDisabledEvents:
    def test_disabled_structure(self):
        section = build_disabled_events(datetime.now())
        assert section["source_status"] == "disabled"
        assert section["source_error"] is None


class TestBuildNotFoundEvents:
    def test_not_found_structure(self):
        section = build_not_found_events(datetime.now())
        assert section["source_status"] == "available"
        assert section["source_error"] is None
        assert section["total_notices"] == 0


class TestBodaccWithInsee:
    @respx.mock
    def test_insee_ok_bodacc_unavailable(self):
        insee_url = "https://api.insee.fr/api-sirene/3.11/siren/784671695"
        respx.get(insee_url).mock(
            return_value=__import__("httpx").Response(
                200,
                json={
                    "header": {"statut": 200, "message": "OK"},
                    "uniteLegale": {
                        "siren": "784671695",
                        "nicSiegeUniteLegale": "00087",
                        "dateCreationUniteLegale": "1990-01-01",
                        "trancheEffectifsUniteLegale": "22",
                        "anneeEffectifsUniteLegale": "2023",
                        "periodesUniteLegale": [
                            {
                                "dateFin": None,
                                "dateDebut": "2020-01-01",
                                "etatAdministratifUniteLegale": "A",
                                "denominationUniteLegale": "UNICEF FRANCE",
                                "activitePrincipaleUniteLegale": "94.99Z",
                                "nicSiegeUniteLegale": "00087",
                            }
                        ],
                    },
                },
            )
        )
        siege_url = "https://api.insee.fr/api-sirene/3.11/siret/78467169500087"
        respx.get(siege_url).mock(
            return_value=__import__("httpx").Response(
                200,
                json={
                    "header": {"statut": 200, "message": "OK"},
                    "etablissement": {
                        "siren": "784671695",
                        "nic": "00087",
                        "siret": "78467169500087",
                        "adresseEtablissement": {
                            "numeroVoieEtablissement": "12",
                            "typeVoieEtablissement": "RUE",
                            "libelleVoieEtablissement": "DE LA PAIX",
                            "codePostalEtablissement": "75001",
                            "libelleCommuneEtablissement": "PARIS",
                        },
                        "uniteLegale": {
                            "etatAdministratifUniteLegale": "A",
                            "dateCreationUniteLegale": "1990-01-10",
                            "categorieJuridiqueUniteLegale": "9220",
                            "denominationUniteLegale": "UNICEF FRANCE",
                        },
                    },
                },
            )
        )
        bodacc_url = "https://bodacc-datadila.opendatasoft.com/api/explore/v2.0/catalog/datasets/annonces-commerciales/records"
        respx.get(bodacc_url).mock(
            side_effect=__import__("httpx").TimeoutException("timeout")
        )

        adapter = InseeAdapter(api_key="test-key")
        result = adapter.lookup_with_bodacc("784671695")
        assert result.siren == "784671695"
        assert result.legal_name == "UNICEF FRANCE"
        assert result.events["source_status"] == "unavailable"
        assert result.events["source_error"] == ERROR_TIMEOUT

    @respx.mock
    def test_insee_ok_bodacc_5xx(self):
        insee_url = "https://api.insee.fr/api-sirene/3.11/siren/784671695"
        respx.get(insee_url).mock(
            return_value=__import__("httpx").Response(
                200,
                json={
                    "header": {"statut": 200, "message": "OK"},
                    "uniteLegale": {
                        "siren": "784671695",
                        "dateCreationUniteLegale": "1990-01-01",
                        "trancheEffectifsUniteLegale": "22",
                        "anneeEffectifsUniteLegale": "2023",
                        "periodesUniteLegale": [
                            {
                                "dateFin": None,
                                "dateDebut": "2020-01-01",
                                "etatAdministratifUniteLegale": "A",
                                "denominationUniteLegale": "UNICEF FRANCE",
                                "activitePrincipaleUniteLegale": "94.99Z",
                            }
                        ],
                    },
                },
            )
        )
        bodacc_url = "https://bodacc-datadila.opendatasoft.com/api/explore/v2.0/catalog/datasets/annonces-commerciales/records"
        respx.get(bodacc_url).mock(
            return_value=__import__("httpx").Response(500, text="error")
        )

        adapter = InseeAdapter(api_key="test-key", fetch_siege_for_siren=False)
        result = adapter.lookup_with_bodacc("784671695")
        assert result.siren == "784671695"
        assert result.events["source_status"] == "unavailable"
        assert result.events["source_error"] == ERROR_HTTP_5XX

    @respx.mock
    def test_insee_ok_bodacc_no_results(self):
        insee_url = "https://api.insee.fr/api-sirene/3.11/siren/784671695"
        respx.get(insee_url).mock(
            return_value=__import__("httpx").Response(
                200,
                json={
                    "header": {"statut": 200, "message": "OK"},
                    "uniteLegale": {
                        "siren": "784671695",
                        "dateCreationUniteLegale": "1990-01-01",
                        "trancheEffectifsUniteLegale": "22",
                        "anneeEffectifsUniteLegale": "2023",
                        "periodesUniteLegale": [
                            {
                                "dateFin": None,
                                "dateDebut": "2020-01-01",
                                "etatAdministratifUniteLegale": "A",
                                "denominationUniteLegale": "UNICEF FRANCE",
                                "activitePrincipaleUniteLegale": "94.99Z",
                            }
                        ],
                    },
                },
            )
        )
        bodacc_url = "https://bodacc-datadila.opendatasoft.com/api/explore/v2.0/catalog/datasets/annonces-commerciales/records"
        respx.get(bodacc_url).mock(
            return_value=__import__("httpx").Response(
                200, json={"total_count": 0, "records": []}
            )
        )

        adapter = InseeAdapter(api_key="test-key", fetch_siege_for_siren=False)
        result = adapter.lookup_with_bodacc("784671695")
        assert result.siren == "784671695"
        assert result.events["source_status"] == "available"
        assert result.events["total_notices"] == 0
        assert result.events["source_error"] is None
