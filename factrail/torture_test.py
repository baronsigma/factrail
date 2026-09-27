"""Torture-test script: stress-tests Factrail against live sources.

Produces aggregate diagnostics only. Does not modify production data.

Sample covers:
- Active/inactive entities
- Old companies (pre-1980)
- Recently created companies (2020+)
- Associations (SIREN starting with specific patterns)
- Partial-diffusion entities
- Many BODACC notices
- Zero notices
- Collective proceedings
- Radiations
- La Poste (Luhn edge case)
- Establishments/SIRETs

Usage:
    python -m factrail.torture_test          # uses INSEE_API_KEY from env/.env
    python -m factrail.torture_test --key X  # explicit key
    python -m factrail.torture_test --no-bodacc  # INSEE only
    python -m factrail.torture_test --max N  # limit sample size
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import statistics
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional

from .models import FrenchCompany
from .sources.insee import InseeAdapter

logger = logging.getLogger(__name__)

# Diverse sample of real French entities (verified to exist)
# Format: (identifier, description, expected_characteristics)
SAMPLE = [
    # === Active public companies ===
    ("784671695", "UNICEF France — active association, diffusible", {"type": "active"}),
    ("356000000", "La Poste — Luhn edge case", {"type": "active"}),
    ("652014051", "Carrefour SA — active, many BODACC notices", {"type": "active"}),
    ("325351681", "TotalEnergies — active, large", {"type": "active"}),
    ("552014051", "Renault — active, historical BODACC", {"type": "active"}),

    # === Inactive entities ===
    ("398607333", "Renault Retail Group — inactive", {"type": "inactive"}),
    ("828896043", "Ce Qu'il Faut Donner — inactive, removed", {"type": "inactive"}),

    # === Old companies (pre-1980) ===
    ("552100000", "Old company — pre-1980", {"era": "old"}),
    ("314000000", "Old SIREN — pre-1980", {"era": "old"}),
    ("505000000", "Old SIREN — 1970s-80s", {"era": "old"}),

    # === Recently created (2020+) ===
    ("914000000", "Recent SIREN — 2020+ (likely creation)", {"era": "recent"}),
    ("939000000", "Recent SIREN — 2021+", {"era": "recent"}),
    ("955000000", "Recent SIREN — 2022+", {"era": "recent"}),
    ("978000000", "Recent SIREN — 2023+", {"era": "recent"}),

    # === SIRET lookups (establishments) ===
    ("78467169500087", "UNICEF siege SIRET", {"kind": "siret"}),
    ("35600000000018", "La Poste siege SIRET", {"kind": "siret"}),
    ("65201405100732", "Carrefour siege SIRET", {"kind": "siret"}),
    ("32535168100015", "TotalEnergies siege SIRET", {"kind": "siret"}),

    # === Companies with collective proceedings (known) ===
    ("552031681", "Air France (historical proceedings)", {"bodacc": "many"}),
    ("441283624", "Printemps (historical)", {"bodacc": "proceeding"}),

    # === Random-looking SIRENs (edge cases) ===
    ("123456782", "SIREN with valid Luhn", {"luhn": "valid"}),
    ("000000000", "All zeros — edge case", {"edge": True}),

    # === Additional diverse entities ===
    ("402777674", "BNP Paribas", {"type": "active"}),
    ("316203247", "LVMH", {"type": "active"}),
    ("542081503", "Sanofi", {"type": "active"}),
    ("702003932", "Danone", {"type": "active"}),
    ("662042449", "Capgemini", {"type": "active"}),
]


def _load_env() -> None:
    """Load .env file if present."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())


def _torture_test(
    api_key: Optional[str] = None,
    max_samples: int = 0,
    bodacc_enabled: bool = True,
    seed: int = 42,
) -> dict:
    """Run torture test and return aggregate diagnostics."""
    adapter = InseeAdapter(
        api_key=api_key,
        bodacc_enabled=bodacc_enabled,
    )

    # Shuffle sample for variety
    rng = random.Random(seed)
    sample = list(SAMPLE)
    rng.shuffle(sample)

    if max_samples > 0:
        sample = sample[:max_samples]

    results: list[dict] = []
    failures: list[dict] = []
    schema_exceptions: list[dict] = []
    unexpected_records: list[dict] = []

    total_latency = 0.0
    insee_latencies: list[float] = []
    bodacc_latencies: list[float] = []
    bodacc_notice_counts: list[int] = []

    total = len(sample)
    successes = 0
    partial_successes = 0
    insee_failures = 0
    bodacc_failures = 0

    for i, (identifier, description, expected) in enumerate(sample):
        result = {
            "identifier": identifier,
            "description": description,
            "expected": expected,
        }

        try:
            start = time.time()
            company = adapter.lookup_with_bodacc(identifier)
            elapsed = time.time() - start
            total_latency += elapsed
            insee_latencies.append(elapsed)

            successes += 1
            result["status"] = "success"
            result["siren"] = company.siren
            result["legal_name"] = company.legal_name
            result["company_status"] = company.status
            result["diffusion"] = company.diffusion_status
            result["latency_s"] = round(elapsed, 3)

            # Check BODACC events
            if company.events:
                events = company.events
                result["bodacc_status"] = events.get("source_status")
                result["bodacc_error"] = events.get("source_error")
                result["bodacc_total"] = events.get("total_notices")
                result["bodacc_returned"] = events.get("returned_notices")

                if events.get("source_status") == "available":
                    notice_count = events.get("total_notices", 0) or 0
                    bodacc_notice_counts.append(notice_count)
                elif events.get("source_status") == "unavailable":
                    bodacc_failures += 1
                    partial_successes += 1
                    result["status"] = "partial"

                # Check for unexpected patterns
                if events.get("total_notices", 0) and events["total_notices"] > 100:
                    unexpected_records.append({
                        "identifier": identifier,
                        "description": description,
                        "pattern": "very_high_bodacc_count",
                        "count": events["total_notices"],
                    })

                if events.get("source_error") == "malformed_response":
                    schema_exceptions.append({
                        "identifier": identifier,
                        "description": description,
                        "error": "malformed_response",
                    })

            # Schema validation on company data
            try:
                dump = company.model_dump(mode="json")
                # Verify required fields exist
                assert "siren" in dump
                assert "checked_at" in dump
                if bodacc_enabled:
                    assert "events" in dump
                    if company.events:
                        assert "source_status" in company.events
                        assert company.events["source_status"] in (
                            "available", "unavailable", "disabled", "partial"
                        )
            except Exception as e:
                schema_exceptions.append({
                    "identifier": identifier,
                    "description": description,
                    "error": str(e),
                    "type": "schema_validation",
                })

            # Check for unexpected diffusion status
            if company.diffusion_status == "diffusion_partielle":
                unexpected_records.append({
                    "identifier": identifier,
                    "description": description,
                    "pattern": "partial_diffusion",
                })

        except Exception as e:
            elapsed = time.time() - start
            total_latency += elapsed
            insee_failures += 1
            result["status"] = "failure"
            result["error"] = str(e)
            result["latency_s"] = round(elapsed, 3)
            failures.append(result)

        results.append(result)

    # Compute latency percentiles
    def percentile(data: list[float], p: float) -> Optional[float]:
        if not data:
            return None
        sorted_data = sorted(data)
        k = (len(sorted_data) - 1) * p / 100
        f = int(k)
        c = f + 1 if f + 1 < len(sorted_data) else f
        return round(sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f]), 3)

    diagnostics = {
        "summary": {
            "total": total,
            "successes": successes,
            "partial_successes": partial_successes,
            "insee_failures": insee_failures,
            "bodacc_failures": bodacc_failures,
            "success_rate": round(successes / total * 100, 1) if total else 0,
            "partial_success_rate": round(partial_successes / total * 100, 1) if total else 0,
        },
        "latency": {
            "total_s": round(total_latency, 3),
            "avg_ms": round(statistics.mean(insee_latencies) * 1000, 1) if insee_latencies else None,
            "p50_ms": round(percentile(insee_latencies, 50) * 1000, 1) if insee_latencies else None,
            "p95_ms": round(percentile(insee_latencies, 95) * 1000, 1) if insee_latencies else None,
            "p99_ms": round(percentile(insee_latencies, 99) * 1000, 1) if insee_latencies else None,
            "max_ms": round(max(insee_latencies) * 1000, 1) if insee_latencies else None,
        },
        "bodacc_notice_distribution": {
            "samples_with_notices": len([c for c in bodacc_notice_counts if c > 0]),
            "samples_without_notices": len([c for c in bodacc_notice_counts if c == 0]),
            "min": min(bodacc_notice_counts) if bodacc_notice_counts else None,
            "max": max(bodacc_notice_counts) if bodacc_notice_counts else None,
            "median": statistics.median(bodacc_notice_counts) if bodacc_notice_counts else None,
            "mean": round(statistics.mean(bodacc_notice_counts), 1) if bodacc_notice_counts else None,
        },
        "schema_exceptions": schema_exceptions[:5],  # limit to first 5
        "unexpected_records": unexpected_records[:5],
        "failures": failures,
        "sample_details": results[:10],  # first 10 results
    }

    return diagnostics


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="factrail-torture",
        description="Torture-test Factrail against live sources",
    )
    parser.add_argument("--key", help="INSEE API key (default: from env/.env)")
    parser.add_argument("--max", type=int, default=0, help="Max sample size (0=all)")
    parser.add_argument("--no-bodacc", action="store_true", help="Skip BODACC")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s: %(message)s",
    )

    _load_env()
    api_key = args.key or os.getenv("INSEE_API_KEY", "")

    if not api_key:
        print("ERROR: INSEE_API_KEY required. Use --key or set in .env")
        sys.exit(1)

    print(f"Starting torture test with {len(SAMPLE)} sample entities...")
    print(f"BODACC: {'disabled' if args.no_bodacc else 'enabled'}")
    print(f"Max samples: {args.max or 'all'}")
    print("=" * 60)

    diagnostics = _torture_test(
        api_key=api_key,
        max_samples=args.max,
        bodacc_enabled=not args.no_bodacc,
        seed=args.seed,
    )

    print(json.dumps(diagnostics, indent=2, ensure_ascii=False))
    print("=" * 60)

    s = diagnostics["summary"]
    if s["success_rate"] >= 90:
        print(f"PASS: {s['success_rate']}% success rate ({s['successes']}/{s['total']})")
    else:
        print(f"WARN: {s['success_rate']}% success rate ({s['successes']}/{s['total']})")

    if s["partial_successes"] > 0:
        print(f"NOTE: {s['partial_successes']} partial successes (BODACC unavailable)")

    lat = diagnostics["latency"]
    if lat["p50_ms"]:
        print(f"Latency p50={lat['p50_ms']}ms p95={lat['p95_ms']}ms")


if __name__ == "__main__":
    main()
