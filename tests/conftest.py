"""Pytest configuration for Factrail."""

from __future__ import annotations

import os

import pytest


def pytest_addoption(parser):
    """Add custom command-line options."""
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Run live integration tests (requires network + API key)"
    )
    parser.addoption(
        "--torture",
        action="store_true",
        default=False,
        help="Run torture tests (slow, hits live sources)"
    )


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "live: mark test as a live integration test (requires network + API key)"
    )
    config.addinivalue_line(
        "markers", "torture: mark test as a torture test (slow, hits live sources)"
    )


def pytest_collection_modifyitems(config, items):
    """Skip live/torture tests unless explicitly requested."""
    run_live = config.getoption("--live", default=False)
    run_torture = config.getoption("--torture", default=False)

    for item in items:
        if "live" in item.keywords and not run_live:
            item.add_marker(
                pytest.mark.skip(reason="Live tests skipped. Use --live to enable.")
            )
        if "torture" in item.keywords and not run_torture:
            item.add_marker(
                pytest.mark.skip(reason="Torture tests skipped. Use --torture to enable.")
            )
