"""Opt-in checks of the Commission TARIC source landing page only.

Run with ``pytest --live tests/test_live_taric.py``. This does not query the
human tariff consultation UI or assert a tariff value.
"""
import httpx
import pytest


@pytest.mark.live
def test_commission_taric_page_advertises_free_raw_excel_data():
    response = httpx.get(
        "https://taxation-customs.ec.europa.eu/online-services/online-services-and-databases-customs/eu-customs-tariff-taric_en",
        timeout=20,
        follow_redirects=True,
    )
    assert response.status_code == 200
    text = response.text.lower()
    assert "taric" in text
    assert "excel" in text
    assert "circabc.europa.eu" in text
