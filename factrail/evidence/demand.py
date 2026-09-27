"""Privacy-limited demand events for Evidence Core operations."""
from __future__ import annotations

import json
import hashlib
import logging
import os
import re
import sqlite3
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)
TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _safe(value: object) -> str:
    return value if isinstance(value, str) and TOKEN.fullmatch(value) else "invalid_or_other"


KNOWN_CAPABILITIES = {"company_fr", "import", "trade", "vat", "sanctions", "regulation", "product", "news", "web", "beneficial_ownership"}
KNOWN_FIELDS = {"status", "legal_name", "legal_form", "head_office", "naf_code", "siren", "siret",
                "trade_name", "creation_date", "cessation_date", "diffusion_status", "product",
                "origin_country", "destination_country", "quantity", "goods_value", "currency",
                "known_hs_code", "material", "weight_kg", "dimensions", "manufacturer", "model",
                "incoterm", "shipping_mode", "origin_location", "destination_location", "freight_cost",
                "insurance_cost", "hs_code", "base_duty_rate_pct", "import_vat_rate_pct",
                "estimated_total", "food_contact"}


def _category(value: object, allowed: set[str]) -> str:
    if isinstance(value, str) and value in allowed:
        return value
    if isinstance(value, str) and TOKEN.fullmatch(value):
        return "other_" + hashlib.sha256(value.encode()).hexdigest()[:12]
    return "invalid_or_other"


def _safe_list(values: object) -> list[str]:
    return sorted({_category(v, KNOWN_FIELDS) for v in values}) if isinstance(values, list) else []


class DemandStore:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or os.environ.get("FACTRAIL_CACHE_PATH", "/tmp/factrail_cache.db")
        with sqlite3.connect(self.db_path, timeout=5) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS evidence_demand (
                id INTEGER PRIMARY KEY, timestamp REAL NOT NULL, operation TEXT NOT NULL,
                capability TEXT NOT NULL, requested_fields TEXT NOT NULL,
                outcome TEXT NOT NULL, coverage TEXT, unresolved_fields TEXT NOT NULL,
                unsupported_capability INTEGER NOT NULL, source_failures TEXT NOT NULL,
                latency_ms REAL NOT NULL, cache_status TEXT)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_evidence_demand_time ON evidence_demand(timestamp)")

    def record(self, *, operation: str, capability: object, requested_fields: object = None,
               outcome: str, coverage: str | None = None, unresolved_fields: object = None,
               unsupported_capability: bool = False, source_failures: object = None,
               latency_ms: float = 0.0, cache_status: str | None = None) -> None:
        with sqlite3.connect(self.db_path, timeout=5) as conn:
            conn.execute("""INSERT INTO evidence_demand
                (timestamp,operation,capability,requested_fields,outcome,coverage,unresolved_fields,
                 unsupported_capability,source_failures,latency_ms,cache_status)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
                time.time(), _safe(operation), _category(capability, KNOWN_CAPABILITIES), json.dumps(_safe_list(requested_fields)),
                _safe(outcome), _safe(coverage) if coverage else None,
                json.dumps(_safe_list(unresolved_fields)), int(unsupported_capability),
                json.dumps(sorted({_safe(v) for v in source_failures}) if isinstance(source_failures, list) else []), max(0.0, latency_ms),
                _safe(cache_status) if cache_status else None))

    def report(self, days: int = 7) -> dict:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
        with sqlite3.connect(self.db_path, timeout=5) as conn:
            rows = conn.execute("""SELECT operation,capability,requested_fields,outcome,coverage,
                unresolved_fields,unsupported_capability,source_failures FROM evidence_demand
                WHERE timestamp >= ?""", (cutoff,)).fetchall()
        calls = Counter((row[0], row[1]) for row in rows)
        outcomes = Counter(row[3] for row in rows)
        requested = Counter(v for row in rows for v in json.loads(row[2]))
        unresolved = Counter(v for row in rows for v in json.loads(row[5]))
        unsupported = Counter(row[1] for row in rows if row[6])
        failures = Counter(v for row in rows for v in json.loads(row[7]))
        coverage = Counter(row[4] for row in rows if row[4] in ("partial", "insufficient"))
        return {"period_days": days, "total_calls": len(rows),
                "calls_by_operation_capability": [{"operation": a, "capability": b, "count": n} for (a,b),n in sorted(calls.items())],
                "outcomes": dict(sorted(outcomes.items())), "requested_fields": dict(requested.most_common()),
                "unresolved_fields": dict(unresolved.most_common()),
                "unsupported_capabilities": dict(unsupported.most_common()),
                "source_failures": dict(failures.most_common()), "incomplete_coverage": dict(coverage)}


def record_safely(**kwargs: object) -> None:
    try:
        DemandStore().record(**kwargs)
    except Exception as exc:
        logger.warning("Evidence demand telemetry write failed: %s", exc)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="FACTRAIL Evidence Core demand report")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    print(json.dumps(DemandStore().report(days=max(1, args.days)), indent=2))
