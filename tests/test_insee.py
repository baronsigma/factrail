"""Tests for Factrail — INSEE adapter validation, normalization, and HTTP handling.

Uses respx to mock httpx — tests never hit the live API.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
import respx

from factrail.models import FrenchCompany, mark_null
from factrail.normalize import normalize_siren_response, normalize_siret_response
from factrail.sources.insee import (
    InseeAdapter,
    NotFoundError,
    UpstreamError,
    ValidationError,
)


# ---------------------------------------------------------------------------
# Null semantics tests
# ---------------------------------------------------------------------------


class TestNullSemantics:
    def test_null_reason_emitted(self):
        fc = FrenchCompany(siren="123456789", checked_at=datetime.now())
        mark_null(fc, "legal_name", "unknown")
        data = fc.model_dump(mode="json")
        assert "legal_name_reason" in data
        assert data["legal_name_reason"] == "unknown"

    def test_unavailable_null(self):
        fc = FrenchCompany(siren="123456789", checked_at=datetime.now())
        mark_null(fc, "address", "unavailable")
        data = fc.model_dump(mode="json")
        assert data["address_reason"] == "unavailable"

    def test_present_value_no_reason(self):
        fc = FrenchCompany(siren="123456789", legal_name="ACME", checked_at=datetime.now())
        data = fc.model_dump(mode="json")
        assert data["legal_name"] == "ACME"
        assert "legal_name_reason" not in data


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


class TestSirenValidation:
    @pytest.mark.parametrize(
        "siren,expected",
        [
            ("784671695", True),  # UNICEF
            ("356000000", True),  # La Poste
            ("123456789", False),  # bad Luhn
            ("123", False),  # wrong length
            ("1234567890", False),  # 10 digits
            ("abcdefghij", False),  # not digits
        ],
    )
    def test_validate_siren(self, siren, expected):
        assert InseeAdapter.validate_siren(siren) is expected


class TestSiretValidation:
    @pytest.mark.parametrize(
        "siret,expected",
        [
            ("39860733300059", True),
            ("12345678901234", False),  # bad Luhn
            ("123", False),  # wrong length
        ],
    )
    def test_validate_siret(self, siret, expected):
        assert InseeAdapter.validate_siret(siret) is expected


class TestClassify:
    def test_siren(self):
        assert InseeAdapter.classify("784671695") == "siren"

    def test_siret(self):
        assert InseeAdapter.classify("39860733300059") == "siret"

    def test_invalid_raises(self):
        with pytest.raises(ValidationError):
            InseeAdapter.classify("123")

    def test_spaces_stripped(self):
        assert InseeAdapter.classify("784 671 695") == "siren"


# ---------------------------------------------------------------------------
# Normalization tests (no HTTP)
# ---------------------------------------------------------------------------


class TestNormalizeSirenResponse:
    RAW = {
        "header": {"statut": 200, "message": "OK"},
        "uniteLegale": {
            "siren": "784671695",
            "statutDiffusionUniteLegale": "O",
            "dateCreationUniteLegale": "1990-01-01",
            "trancheEffectifsUniteLegale": "01",
            "anneeEffectifsUniteLegale": "2023",
            "categorieJuridiqueUniteLegale": "9220",
            "periodesUniteLegale": [
                {
                    "dateFin": None,
                    "dateDebut": "2020-01-01",
                    "etatAdministratifUniteLegale": "A",
                    "denominationUniteLegale": "COMITE FRANCAIS POUR L'UNICEF",
                    "nomUniteLegale": None,
                    "activitePrincipaleUniteLegale": "94.99Z",
                    "nomenclatureActivitePrincipaleUniteLegale": "NAFRev2",
                }
            ],
        },
    }

    def test_basic(self):
        r = normalize_siren_response(self.RAW)
        assert r.siren == "784671695"
        assert r.legal_name == "COMITE FRANCAIS POUR L'UNICEF"
        assert r.status == "active"
        assert r.diffusion_status == "diffusible"
        assert r.legal_form_code == "9220"
        assert r.creation_date == "1990-01-01"
        assert r.naf_code == "94.99Z"
        assert r.naf_code_nomenclature == "NAFRev2"
        assert r.employee_band == "1 ou 2 salariés"
        assert r.employee_band_year == 2023

    def test_null_reason_in_output(self):
        r = normalize_siren_response(self.RAW)
        data = r.model_dump(mode="json")
        # nomUniteLegale was null in the input → unknown
        assert data["trade_name_reason"] == "unknown"

    def test_no_address_without_siege(self):
        r = normalize_siren_response(self.RAW)
        data = r.model_dump(mode="json")
        assert data["address_reason"] == "unavailable"

    def test_with_siege(self):
        SIEGE_RAW = {
            "header": {"statut": 200, "message": "OK"},
            "etablissement": {
                "siren": "784671695",
                "nic": "00001",
                "siret": "78467169500001",
                "adresseEtablissement": {
                    "numeroVoieEtablissement": "12",
                    "typeVoieEtablissement": "RUE",
                    "libelleVoieEtablissement": "DE LA PAIX",
                    "codePostalEtablissement": "75001",
                    "libelleCommuneEtablissement": "PARIS",
                },
                "uniteLegale": {
                    "etatAdministratifUniteLegale": "A",
                    "dateCreationUniteLegale": "1990-01-01",
                    "categorieJuridiqueUniteLegale": "9220",
                    "denominationUniteLegale": "COMITE FRANCAIS POUR L'UNICEF",
                },
            },
        }
        r = normalize_siren_response(self.RAW, siege_raw=SIEGE_RAW)
        assert r.address is not None
        assert "LA PAIX" in r.address.line_1
        assert r.siret == "78467169500001"

    def test_period_sorting(self):
        raw = json.loads(json.dumps(self.RAW))
        raw["uniteLegale"]["periodesUniteLegale"] = [
            {
                "dateFin": None,
                "dateDebut": "2024-06-01",
                "etatAdministratifUniteLegale": "A",
                "denominationUniteLegale": "NEW NAME",
                "activitePrincipaleUniteLegale": "62.01Z",
                "nomenclatureActivitePrincipaleUniteLegale": "NAFRev2",
            },
            {
                "dateFin": "2024-05-31",
                "dateDebut": "2000-01-01",
                "etatAdministratifUniteLegale": "A",
                "denominationUniteLegale": "OLD NAME",
                "activitePrincipaleUniteLegale": "94.99Z",
                "nomenclatureActivitePrincipaleUniteLegale": "NAFRev2",
            },
        ]
        r = normalize_siren_response(raw)
        assert r.legal_name == "NEW NAME"
        assert r.naf_code == "62.01Z"

    def test_inactive(self):
        raw = json.loads(json.dumps(self.RAW))
        raw["uniteLegale"]["periodesUniteLegale"][0]["etatAdministratifUniteLegale"] = "C"
        raw["uniteLegale"]["periodesUniteLegale"][0]["dateFin"] = "2024-06-01"
        r = normalize_siren_response(raw)
        assert r.status == "inactive"
        assert r.cessation_date == "2024-06-01"

    def test_sources_present(self):
        r = normalize_siren_response(self.RAW)
        assert len(r.sources) >= 1
        assert r.sources[0].id == "insee-siren"
        assert isinstance(r.checked_at, datetime)


class TestNormalizeSiretResponse:
    RAW = {
        "header": {"statut": 200, "message": "OK"},
        "etablissement": {
            "siren": "398607333",
            "nic": "00059",
            "siret": "39860733300059",
            "statutDiffusionEtablissement": "O",
            "dateCreationEtablissement": "2015-01-09",
            "trancheEffectifsEtablissement": "01",
            "anneeEffectifsEtablissement": "2016",
            "activitePrincipaleEtablissement": "47.81Z",
            "nomenclatureActivitePrincipaleEtablissement": "NAFRev2",
            "adresseEtablissement": {
                "numeroVoieEtablissement": "44",
                "typeVoieEtablissement": "RUE",
                "libelleVoieEtablissement": "NANTIER DIDIEE",
                "codePostalEtablissement": "97490",
                "libelleCommuneEtablissement": "SAINT DENIS",
            },
            "uniteLegale": {
                "etatAdministratifUniteLegale": "C",
                "statutDiffusionUniteLegale": "O",
                "dateCreationUniteLegale": "1994-10-10",
                "categorieJuridiqueUniteLegale": "1000",
                "denominationUniteLegale": "GRONDIN",
                "trancheEffectifsUniteLegale": "01",
                "anneeEffectifsUniteLegale": "2016",
            },
        },
    }

    def test_basic(self):
        r = normalize_siret_response(self.RAW)
        assert r.siren == "398607333"
        assert r.siret == "39860733300059"
        assert r.diffusion_status == "diffusible"
        assert r.legal_form_code == "1000"
        assert r.creation_date == "1994-10-10"
        assert r.naf_code == "47.81Z"
        assert r.naf_code_nomenclature == "NAFRev2"
        assert r.address is not None
        assert r.address.postal_code == "97490"
        assert "NANTIER" in r.address.line_1

    def test_active_establishment(self):
        raw = json.loads(json.dumps(self.RAW))
        raw["etablissement"]["uniteLegale"]["etatAdministratifUniteLegale"] = "A"
        r = normalize_siret_response(raw)
        assert r.status == "active"


# ---------------------------------------------------------------------------
# HTTP tests (mocked)
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter():
    return InseeAdapter(
        api_key="test-key",
        base_url="https://api.insee.fr/api-sirene/3.11",
        fetch_siege_for_siren=False,
    )


class TestHttpAdapter:
    RAW_SIREN = {
        "header": {"statut": 200, "message": "OK"},
        "uniteLegale": {
            "siren": "784671695",
            "nicSiegeUniteLegale": "00001",
            "dateCreationUniteLegale": "1990-01-01",
            "trancheEffectifsUniteLegale": "01",
            "anneeEffectifsUniteLegale": "2023",
            "categorieJuridiqueUniteLegale": "9220",
            "periodesUniteLegale": [
                {
                    "dateFin": None,
                    "dateDebut": "2020-01-01",
                    "etatAdministratifUniteLegale": "A",
                    "denominationUniteLegale": "UNICEF FRANCE",
                    "activitePrincipaleUniteLegale": "94.99Z",
                    "nomenclatureActivitePrincipaleUniteLegale": "NAFRev2",
                }
            ],
        },
    }

    RAW_SIRET = {
        "header": {"statut": 200, "message": "OK"},
        "etablissement": {
            "siren": "398607333",
            "nic": "00059",
            "siret": "39860733300059",
            "dateCreationEtablissement": "2015-01-09",
            "trancheEffectifsEtablissement": "01",
            "adresseEtablissement": {
                "numeroVoieEtablissement": "44",
                "typeVoieEtablissement": "RUE",
                "libelleVoieEtablissement": "NANTIER DIDIEE",
                "codePostalEtablissement": "97490",
                "libelleCommuneEtablissement": "SAINT DENIS",
            },
            "uniteLegale": {
                "etatAdministratifUniteLegale": "A",
                "dateCreationUniteLegale": "1994-10-10",
                "categorieJuridiqueUniteLegale": "1000",
                "denominationUniteLegale": "GRONDIN",
                "trancheEffectifsUniteLegale": "01",
                "anneeEffectifsUniteLegale": "2016",
            },
        },
    }

    @respx.mock
    def test_lookup_siren_success(self, adapter):
        route = respx.get("https://api.insee.fr/api-sirene/3.11/siren/784671695").mock(
            return_value=__import__("httpx").Response(200, json=self.RAW_SIREN)
        )
        r = adapter.lookup("784671695")
        assert route.called
        assert r.siren == "784671695"
        assert r.legal_name == "UNICEF FRANCE"

    @respx.mock
    def test_lookup_siret_success(self, adapter):
        route = respx.get("https://api.insee.fr/api-sirene/3.11/siret/39860733300059").mock(
            return_value=__import__("httpx").Response(200, json=self.RAW_SIRET)
        )
        r = adapter.lookup("39860733300059")
        assert route.called
        assert r.siret == "39860733300059"
        assert r.siren == "398607333"

    @respx.mock
    def test_lookup_not_found(self, adapter):
        respx.get("https://api.insee.fr/api-sirene/3.11/siren/000000000").mock(
            return_value=__import__("httpx").Response(404, json={"header": {"statut": 404}})
        )
        with pytest.raises(NotFoundError):
            adapter.lookup("000000000")

    @respx.mock
    def test_lookup_unauthorized(self, adapter):
        respx.get("https://api.insee.fr/api-sirene/3.11/siren/784671695").mock(
            return_value=__import__("httpx").Response(401, json={"header": {"statut": 401}})
        )
        with pytest.raises(UpstreamError, match="API key invalid"):
            adapter.lookup("784671695")

    @respx.mock
    def test_lookup_upstream_500(self, adapter):
        respx.get("https://api.insee.fr/api-sirene/3.11/siren/784671695").mock(
            return_value=__import__("httpx").Response(500, text="boom")
        )
        with pytest.raises(UpstreamError):
            adapter.lookup("784671695")

    @respx.mock
    def test_lookup_timeout_retry(self, adapter):
        route = respx.get("https://api.insee.fr/api-sirene/3.11/siren/784671695").mock(
            side_effect=__import__("httpx").TimeoutException("timeout")
        )
        with pytest.raises(UpstreamError, match="unreachable"):
            adapter.lookup("784671695")
        assert route.call_count == 3

    def test_invalid_length_rejected(self, adapter):
        with pytest.raises(ValidationError):
            adapter.lookup("123")

    def test_bad_luhn_not_rejected(self, adapter):
        """Luhn failure is soft — lookup proceeds."""
        # We need to mock the 404 response for this bad SIREN
        import respx as _respx
        with _respx.mock:
            _respx.get("https://api.insee.fr/api-sirene/3.11/siren/123456789").mock(
                return_value=__import__("httpx").Response(404, json={})
            )
            with pytest.raises(NotFoundError):
                adapter.lookup("123456789")


# ---------------------------------------------------------------------------
# Siege lookup tests
# ---------------------------------------------------------------------------


class TestSiegeLookup:
    RAW_SIREN = {
        "header": {"statut": 200, "message": "OK"},
        "uniteLegale": {
            "siren": "784671695",
            "dateCreationUniteLegale": "1990-01-01",
            "trancheEffectifsUniteLegale": "01",
            "anneeEffectifsUniteLegale": "2023",
            "periodesUniteLegale": [
                {
                    "dateFin": None,
                    "dateDebut": "2020-01-01",
                    "etatAdministratifUniteLegale": "A",
                    "denominationUniteLegale": "UNICEF FRANCE",
                    "activitePrincipaleUniteLegale": "94.99Z",
                    "nomenclatureActivitePrincipaleUniteLegale": "NAFRev2",
                    "nicSiegeUniteLegale": "00001",
                }
            ],
        },
    }

    RAW_SIEGE = {
        "header": {"statut": 200, "message": "OK"},
        "etablissement": {
            "siren": "784671695",
            "nic": "00001",
            "siret": "78467169500001",
            "adresseEtablissement": {
                "numeroVoieEtablissement": "12",
                "typeVoieEtablissement": "RUE",
                "libelleVoieEtablissement": "DE LA PAIX",
                "codePostalEtablissement": "75001",
                "libelleCommuneEtablissement": "PARIS",
            },
            "uniteLegale": {
                "etatAdministratifUniteLegale": "A",
                "dateCreationUniteLegale": "1990-01-01",
                "categorieJuridiqueUniteLegale": "9220",
                "denominationUniteLegale": "UNICEF FRANCE",
            },
        },
    }

    @respx.mock
    def test_siege_fetched(self):
        adapter = InseeAdapter(
            api_key="test-key",
            base_url="https://api.insee.fr/api-sirene/3.11",
            fetch_siege_for_siren=True,
        )
        respx.get("https://api.insee.fr/api-sirene/3.11/siren/784671695").mock(
            return_value=__import__("httpx").Response(200, json=self.RAW_SIREN)
        )
        respx.get("https://api.insee.fr/api-sirene/3.11/siret/78467169500001").mock(
            return_value=__import__("httpx").Response(200, json=self.RAW_SIEGE)
        )
        r = adapter.lookup("784671695")
        assert r.address is not None
        assert r.siret == "78467169500001"
        assert len(r.sources) == 2
        assert r.sources[0].id == "insee-siren"
        assert r.sources[1].id == "insee-siret"

    @respx.mock
    def test_siege_not_found(self):
        """If the siege SIRET returns 404, we still get the SIREN data."""
        adapter = InseeAdapter(
            api_key="test-key",
            base_url="https://api.insee.fr/api-sirene/3.11",
            fetch_siege_for_siren=True,
        )
        respx.get("https://api.insee.fr/api-sirene/3.11/siren/784671695").mock(
            return_value=__import__("httpx").Response(200, json=self.RAW_SIREN)
        )
        respx.get("https://api.insee.fr/api-sirene/3.11/siret/78467169500001").mock(
            return_value=__import__("httpx").Response(404, json={})
        )
        r = adapter.lookup("784671695")
        assert r.siren == "784671695"
        assert r.address is None  # address unavailable
