"""Deprecated compatibility wrapper for Access2Markets cache seeding.

Use :mod:`factrail.trade.access2markets_seed`; this module does not seed an
official Commission TARIC snapshot.
"""
from .access2markets_seed import main, seed_sample_cache

__all__ = ["main", "seed_sample_cache"]

if __name__ == "__main__":
    raise SystemExit(main())
