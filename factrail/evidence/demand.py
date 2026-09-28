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
ORIGINS = {"external", "internal_test", "live_test", "manual", "inspector", "unknown"}
KNOWN_CAPABILITIES = {"company_fr", "import", "trade", "vat", "regulation", "product", "news", "web", "unsupported"}
KNOWN_OPERATIONS = {"verify", "assess", "get_receipt"}
KNOWN_FIELDS = {"status", "legal_name", "legal_form", "head_office", "naf_code", "siren", "siret", "trade_name", "creation_date", "cessation_date", "diffusion_status", "product", "origin_country", "destination_country", "quantity", "goods_value", "currency", "known_hs_code", "material", "weight_kg", "dimensions", "manufacturer", "model", "incoterm", "shipping_mode", "origin_location", "destination_location", "freight_cost", "insurance_cost", "hs_code", "base_duty_rate_pct", "import_vat_rate_pct", "estimated_total", "food_contact", "electrical", "intended_user", "cosmetic", "pharmaceutical", "authoritative_duty_rate"}
KNOWN_OUTCOMES = {"supported", "contradicted", "insufficient_evidence", "stale", "conflicting_sources", "invalid_input", "validation", "not_found", "upstream", "internal", "error", "unsupported_capability"}
KNOWN_COVERAGE = {"sufficient", "partial", "insufficient"}
KNOWN_SOURCE_FAILURES = {"government_registry", "government_bulletin", "tariff_registry", "curated_or_external_reference", "internal_engine"}
SOURCE_OUTCOMES = {"success", "partial", "source_error", "source_unavailable", "source_not_integrated", "lookup_disabled", "not_required", "blocked_before_source"}
SOURCE_DETAILS = {"response_complete", "response_no_applicable_measures", "response_partial", "parser_no_usable_measures", "parser_error", "upstream_error", "stale_cache_after_refresh_failure", "lookup_blocked_missing_classification", "lookup_disabled", "source_not_integrated"}
UNRESOLVED_REASONS = {"missing_user_input", "unsupported_field", "unsupported_fact", "source_unavailable", "source_error", "source_not_integrated", "lookup_disabled", "classification_required", "not_applicable"}


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
                latency_ms REAL NOT NULL, cache_status TEXT,
                origin_class TEXT NOT NULL DEFAULT 'unknown', client_family TEXT NOT NULL DEFAULT 'unknown',
                server_version TEXT, request_id TEXT, receipt_id TEXT, unresolved_reasons TEXT NOT NULL DEFAULT '{}',
                source_outcomes TEXT NOT NULL DEFAULT '{}', mcp_client_name TEXT, mcp_client_version TEXT,
                source_semantics_version INTEGER NOT NULL DEFAULT 1, source_details TEXT NOT NULL DEFAULT '{}',
                request_support_status TEXT NOT NULL DEFAULT 'unknown', capability_registry_version TEXT)""")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(evidence_demand)")}
            for name, decl in (("origin_class", "TEXT NOT NULL DEFAULT 'unknown'"), ("client_family", "TEXT NOT NULL DEFAULT 'unknown'"),
                               ("server_version", "TEXT"), ("request_id", "TEXT"), ("receipt_id", "TEXT"),
                               ("unresolved_reasons", "TEXT NOT NULL DEFAULT '{}'"), ("source_outcomes", "TEXT NOT NULL DEFAULT '{}'"),
                               ("mcp_client_name", "TEXT"), ("mcp_client_version", "TEXT"),
                               ("source_semantics_version", "INTEGER NOT NULL DEFAULT 1")):
                if name not in columns:
                    conn.execute(f"ALTER TABLE evidence_demand ADD COLUMN {name} {decl}")
            if "source_details" not in columns:
                conn.execute("ALTER TABLE evidence_demand ADD COLUMN source_details TEXT NOT NULL DEFAULT '{}'")
            for name, decl in (("request_support_status", "TEXT NOT NULL DEFAULT 'unknown'"), ("capability_registry_version", "TEXT")):
                if name not in columns:
                    conn.execute(f"ALTER TABLE evidence_demand ADD COLUMN {name} {decl}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_evidence_demand_time ON evidence_demand(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_evidence_demand_origin_time ON evidence_demand(origin_class,timestamp)")

    def record(self, *, operation: str, capability: object, requested_fields: object = None,
               outcome: str, coverage: str | None = None, unresolved_fields: object = None,
               unsupported_capability: bool = False, source_failures: object = None,
               latency_ms: float = 0.0, cache_status: str | None = None, origin_class: str = "unknown",
               client_family: str = "unknown", server_version: str | None = None,
               request_id: str | None = None, receipt_id: str | None = None, unresolved_reasons: object = None,
               source_outcomes: object = None, source_details: object = None, request_support_status: str = "unknown", capability_registry_version: str | None = None, mcp_client_name: str | None = None, mcp_client_version: str | None = None) -> None:
        origin = origin_class if origin_class in ORIGINS else "unknown"
        reasons = unresolved_reasons if isinstance(unresolved_reasons, dict) else {}
        safe_reasons = {_category(k, KNOWN_FIELDS): ("unsupported_fact" if v == "unsupported_field" else v)
                        for k, v in reasons.items() if isinstance(v, str) and v in UNRESOLVED_REASONS}
        outcomes = source_outcomes if isinstance(source_outcomes, dict) else {}
        safe_outcomes = {_category(k, KNOWN_SOURCE_FAILURES): v for k, v in outcomes.items() if isinstance(v, str) and v in SOURCE_OUTCOMES}
        details = source_details if isinstance(source_details, dict) else {}
        safe_details = {_category(k, KNOWN_SOURCE_FAILURES): v for k, v in details.items() if isinstance(v, str) and v in SOURCE_DETAILS}
        failures_in = source_failures if isinstance(source_failures, list) else []
        if isinstance(source_outcomes, dict):
            attempted_failures = {key for key, value in safe_outcomes.items() if value in {"source_error", "source_unavailable"}}
            failures_in = [value for value in failures_in if _category(value, KNOWN_SOURCE_FAILURES) in attempted_failures]
        with sqlite3.connect(self.db_path, timeout=5) as conn:
            conn.execute("""INSERT INTO evidence_demand
                (timestamp,operation,capability,requested_fields,outcome,coverage,unresolved_fields,
                 unsupported_capability,source_failures,latency_ms,cache_status,origin_class,client_family,
                 server_version,request_id,receipt_id,unresolved_reasons,source_outcomes,mcp_client_name,mcp_client_version,source_semantics_version,source_details,request_support_status,capability_registry_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                time.time(), _category(operation, KNOWN_OPERATIONS), _category(capability, KNOWN_CAPABILITIES), json.dumps(_safe_list(requested_fields)),
                _category(outcome, KNOWN_OUTCOMES), _category(coverage, KNOWN_COVERAGE) if coverage else None,
                json.dumps(_safe_list(unresolved_fields)), int(unsupported_capability),
                json.dumps(sorted({_category(v, KNOWN_SOURCE_FAILURES) for v in failures_in})),
                max(0.0, latency_ms), cache_status if cache_status in {"hit", "miss", "stale", "unknown"} else ("unknown" if cache_status else None), origin,
                _category(client_family, {"goose", "claude", "cursor", "inspector", "mcp_inspector", "chatgpt", "vscode", "codex", "unknown", "python", "other"}),
                server_version if isinstance(server_version, str) and len(server_version) <= 32 else None,
                request_id if isinstance(request_id, str) and len(request_id) <= 64 else None,
                receipt_id if isinstance(receipt_id, str) and re.fullmatch(r"fr_[0-9a-f]{64}", receipt_id) else None,
                json.dumps(safe_reasons, sort_keys=True), json.dumps(safe_outcomes, sort_keys=True),
                mcp_client_name if mcp_client_name in {"claude desktop", "claude code", "claude", "chatgpt", "cursor", "visual studio code", "vscode", "codex", "mcp inspector", "@modelcontextprotocol/inspector"} else None,
                mcp_client_version if isinstance(mcp_client_version, str) and re.fullmatch(r"[A-Za-z0-9.+_-]{1,32}", mcp_client_version) else None, 2, json.dumps(safe_details, sort_keys=True),
                request_support_status if request_support_status in {"supported", "unsupported_field", "unsupported_capability", "invalid_input", "unknown"} else "unknown",
                capability_registry_version if capability_registry_version == "2.3" else None))

    def report(self, days: int = 7, origin: str | None = None, all_origins: bool = True) -> dict:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
        with sqlite3.connect(self.db_path, timeout=5) as conn:
            rows = conn.execute("""SELECT operation,capability,requested_fields,outcome,coverage,unresolved_fields,
                unsupported_capability,source_failures,origin_class,unresolved_reasons,source_outcomes,source_semantics_version,source_details,request_support_status,capability_registry_version FROM evidence_demand WHERE timestamp >= ?""", (cutoff,)).fetchall()
        period_rows = list(rows)
        # The CLI passes all_origins=False by default; library callers retain legacy aggregation.
        if origin:
            rows = [r for r in rows if r[8] == origin]
        elif not all_origins:
            rows = [r for r in rows if r[8] == "external"]
        external = sum(r[8] == "external" for r in period_rows)
        tests = sum(r[8] in ("internal_test", "live_test") for r in period_rows)
        unknown = sum(r[8] == "unknown" for r in period_rows)
        calls = Counter((r[0], r[1]) for r in rows)
        outcomes = Counter(r[3] for r in rows)
        requested = Counter(v for r in rows for v in json.loads(r[2]))
        unresolved = Counter(v for r in rows for v in json.loads(r[5]))
        unsupported = Counter(r[1] for r in rows if r[6])
        failures = Counter(v for r in rows if r[11] >= 2 for v in json.loads(r[7]))
        legacy_failure_labels = Counter(v for r in rows if r[11] < 2 for v in json.loads(r[7]))
        gap_counts = Counter()
        source_counts: dict[str, Counter] = {}
        for r in rows:
            cap = r[1]
            reasons = json.loads(r[9])
            outcomes = json.loads(r[10])
            for field, reason in reasons.items():
                if field == "authoritative_duty_rate" and reason in outcomes.values():
                    continue
                if reason in {"missing_user_input", "unsupported_fact", "source_not_integrated", "source_unavailable", "source_error", "lookup_disabled", "classification_required"}:
                    gap_counts[(cap, field, reason)] += 1
            if r[6]: gap_counts[(cap, "capability", "unsupported_capability")] += 1
            for source, result in outcomes.items():
                source_counts.setdefault(source, Counter())[result] += 1
                if result in {"source_error", "source_unavailable", "source_not_integrated", "lookup_disabled"}:
                    gap_counts[(cap, "tariff_lookup" if source == "tariff_registry" else source, result)] += 1
        coverage = Counter(r[4] for r in rows if r[4] in ("partial", "insufficient"))
        unmet = Counter()
        for r in rows:
            cap = r[1]
            for field in json.loads(r[5]): unmet[f"{cap} + unresolved_field:{field}"] += 1
            for source in json.loads(r[7]): unmet[f"{cap} + source_failure:{source}"] += 1
            if r[6]: unmet[f"unsupported_capability:{cap}"] += 1
        return {"period_days": days, "origin_filter": origin or ("all" if all_origins else "external"),
                "total_calls": len(rows), "period_total_calls": len(period_rows), "external_calls": external, "internal_test_calls": tests,
                "unknown_calls": unknown, "calls_by_origin": dict(Counter(r[8] for r in period_rows)),
                "calls_by_operation_capability": [{"operation": a, "capability": b, "count": n} for (a,b),n in sorted(calls.items())],
                "outcomes": dict(sorted(outcomes.items())), "requested_fields": dict(requested.most_common()),
                "unresolved_fields": dict(unresolved.most_common()), "unsupported_capabilities": dict(unsupported.most_common()),
                "source_failures": dict(failures.most_common()), "legacy_source_failure_labels": dict(legacy_failure_labels.most_common()),
                "source_outcomes": {k: dict(v) for k,v in source_counts.items()},
                "source_details": dict(Counter(detail for r in rows for detail in json.loads(r[12]).values())),
                "request_support_status": dict(Counter(r[13] for r in rows)),
                "capability_registry_versions": dict(Counter(r[14] for r in rows if r[14])),
                "gaps_by_category": {reason: [{"capability": cap, "field": field, "count": n} for (cap,field,r),n in gap_counts.most_common() if r == reason]
                    for reason in ("missing_user_input", "unsupported_fact", "source_not_integrated", "source_unavailable", "source_error", "lookup_disabled", "classification_required", "unsupported_capability")},
                "actionable_gaps": [{"capability": cap, "gap": field, "reason": reason, "count": n} for (cap,field,reason),n in gap_counts.most_common()],
                "incomplete_coverage": dict(coverage),
                "top_unmet_demand": [{"item": key, "count": n} for key,n in unmet.most_common(10)]}


def record_safely(**kwargs: object) -> None:
    try: DemandStore().record(**kwargs)
    except Exception as exc: logger.warning("Evidence demand telemetry write failed: %s", exc)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="FACTRAIL Evidence Core demand report")
    parser.add_argument("--days", type=int, default=7)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--origin", choices=sorted(ORIGINS))
    group.add_argument("--all-origins", action="store_true")
    args = parser.parse_args()
    print(json.dumps(DemandStore().report(days=max(1, args.days), origin=args.origin, all_origins=args.all_origins), indent=2))
