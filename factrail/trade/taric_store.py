"""Backward-compatible import shim.

Historically this module exposed Access2Markets HTML lookup code under a TARIC
name. New code must use :mod:`access2markets_store` for that portal and
:mod:`official_taric_store` for Commission TARIC snapshots. ``TaricStore`` stays
only as a deprecated alias so existing internal callers do not break.
"""
from .access2markets_store import (
    Access2MarketsStore,
    TaricParserError,
    TaricUnavailable,
    find_mfn_duty,
    find_preference,
    parse_tariff_rate,
    _explicit_no_measures,
    _parse_a2m_version,
    _parse_measures,
    get_default_store,
    lookup_import_measures,
    lookup_taric_measures,
)

TaricStore = Access2MarketsStore

__all__ = ["Access2MarketsStore", "TaricStore", "TaricParserError", "TaricUnavailable",
           "find_mfn_duty", "find_preference", "parse_tariff_rate",
           "get_default_store", "lookup_import_measures", "lookup_taric_measures"]
