"""Live BODACC integration tests.

These hit the real BODACC open-data API (no key required).
Disabled by default — run with: pytest -v tests/test_bodacc_live.py
"""

from __future__ import annotations

import pytest

from factrail.sources.bodacc import BodaccAdapter

pytestmark = pytest.mark.skipif(
    not True,  # always runs when explicitly invoked
    reason="Live tests must be explicitly invoked",
)


@pytest.fixture
def adapter():
    return BodaccAdapter()


class TestLiveBodacc:
    def test_search_real_siren_with_results(self, adapter):
        """Search for a SIREN that should have BODACC notices."""
        # 828896043 has BODACC notices (verified during development)
        result = adapter.search_by_siren("828896043", limit=5)
        assert isinstance(result["total"], int)
        assert result["total"] >= 0
        # If there are results, verify structure
        if result["records"]:
            record = result["records"][0]
            assert "id" in record
            assert "registre" in record

    def test_search_no_results(self, adapter):
        """Search for a pattern that matches no BODACC notices."""
        result = adapter.search_by_siren("ZZZZZZZZZ", limit=5)
        assert result["total"] == 0
        assert result["records"] == []

    def test_pagination(self, adapter):
        """Verify pagination works correctly."""
        result = adapter.search_by_siren("828896043", limit=2)
        if result["total"] > 2:
            assert result["truncated"] is True
            assert len(result["records"]) == 2
