"""Live integration tests for Factrail.

These tests hit the real INSEE Sirene API and verify that our normalization
produces correct invariants.

Live tests are controlled by --live flag (default: skipped).
To run: pytest --live tests/test_live.py
To run ALL live tests: pytest --live
"""

from __future__ import annotations

import os

import pytest

from factrail.models import FrenchCompany
from factrail.sources.insee import InseeAdapter


def _load_env() -> None:
    """Load .env file if present (explicit behavior)."""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip()
                    if key and value:
                        os.environ.setdefault(key, value)


_load_env()

# Skip the entire module unless --live flag is passed AND INSEE_API_KEY is set
def pytest_configure(config):
    config.addinivalue_line(
        "markers", "live: mark test as a live integration test"
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--live", default=False):
        skip_marker = pytest.mark.skip(reason="Live tests skipped. Use --live to enable.")
        for item in items:
            if "live" in item.keywords:
                item.add_marker(skip_marker)
    elif not os.getenv("INSEE_API_KEY"):
        skip_marker = pytest.mark.skip(reason="INSEE_API_KEY not set. Cannot run live tests.")
        for item in items:
            item.add_marker(skip_marker)


@pytest.fixture
def adapter():
    return InseeAdapter(
        fetch_siege_for_siren=True,
    )


@pytest.mark.live
class TestLiveSiren:
    """Live SIREN lookups against the INSEE API."""

    def test_unicef(self, adapter):
        """UNICEF France — well-known public entity."""
        r = adapter.lookup("784671695")
        assert r.siren == "784671695"
        assert r.legal_name is not None
        assert r.status == "active"
        assert r.diffusion_status == "diffusible"
        assert r.naf_code is not None
        assert r.naf_code_nomenclature == "NAFRev2"
        assert r.employee_band is not None
        assert r.address is not None
        assert r.siret is not None
        assert len(r.sources) == 2
        data = r.model_dump(mode="json")
        assert "trade_name_reason" in data or "trade_name" in data

    def test_la_poste(self, adapter):
        """La Poste — fails our Luhn check but is a valid SIREN."""
        r = adapter.lookup("356000000")
        assert r.siren == "356000000"
        assert r.legal_name is not None
        assert r.status == "active"


@pytest.mark.live
class TestLiveSiret:
    """Live SIRET lookups against the INSEE API."""

    def test_siret_with_address(self, adapter):
        """Verify address is returned for SIRET lookups."""
        r = adapter.lookup("65201405100732")
        assert r.siret == "65201405100732"
        assert r.siren == "652014051"
        assert r.address is not None
        assert r.address.postal_code is not None
        assert r.legal_name is not None

    def test_siret_employee_band(self, adapter):
        """Verify employee band is returned."""
        r = adapter.lookup("39860733300059")
        assert r.employee_band is not None


@pytest.mark.live
class TestLiveInvariants:
    """Verify invariants across live responses."""

    def test_all_responses_have_checked_at(self, adapter):
        r = adapter.lookup("784671695")
        assert r.checked_at is not None
        assert r.checked_at.tzinfo is not None

    def test_naf_code_with_nomenclature(self, adapter):
        r = adapter.lookup("784671695")
        assert r.naf_code is not None
        assert r.naf_code_nomenclature is not None
        assert r.naf_code_nomenclature in ("NAFRev1", "NAFRev2", "NAFRev21")

    def test_diffusion_status_present(self, adapter):
        r = adapter.lookup("784671695")
        assert r.diffusion_status is not None
        assert r.diffusion_status in ("diffusible", "diffusion_partielle")

    def test_null_reasons_valid(self, adapter):
        r = adapter.lookup("784671695")
        valid = {"unknown", "unavailable", "not_applicable"}
        for field_name, reason in r.null_reasons.items():
            assert reason in valid, f"Invalid reason {reason} for {field_name}"

    def test_events_section_present(self, adapter):
        """Verify BODACC events section is included."""
        r = adapter.lookup_with_bodacc("784671695")
        assert r.events is not None
        assert "source_status" in r.events
        assert r.events["source_status"] in ("available", "unavailable", "disabled", "partial")
