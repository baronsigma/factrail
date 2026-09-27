"""Factrail telemetry CLI — report and purge usage statistics.

Usage:
    python3 -m factrail.telemetry report
    python3 -m factrail.telemetry purge
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

from .telemetry import get_telemetry_store, is_telemetry_enabled


def cmd_report(args: argparse.Namespace) -> int:
    """Show telemetry report.

    ``report`` reads the existing telemetry DB and prints a summary
    regardless of whether recording is currently enabled.  Recording state
    and reportability are separate concerns: a user may want to inspect a
    DB that was written while recording was on, even if recording is now
    off.

    ``FACTRAIL_TELEMETRY_SECRET`` is never printed.
    """
    recording = is_telemetry_enabled()
    store = get_telemetry_store()
    now = datetime.now(timezone.utc)

    print(f"Factrail Telemetry Report — {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("=" * 60)
    print()
    print(f"Recording: {'enabled' if recording else 'disabled'}")
    print()

    # Calls summary
    print("CALLS")
    print("-" * 40)
    for label, days in [("Today", 1), ("Last 7 days", 7), ("Last 30 days", 30)]:
        stats = store.get_stats(days)
        print(
            f"  {label:<14} {stats['total_calls']:>6} calls  "
            f"{stats['unique_clients']:>4} unique clients"
        )
    print()

    # Aggregate stats (last 30 days)
    stats = store.get_stats(30)
    print("AGGREGATE (last 30 days)")
    print("-" * 40)
    print(f"  Success rate:      {stats['success_rate']}%")
    print(f"  Cache hit rate:    {stats['cache_hit_rate']}%")
    print(f"  Latency p50:       {stats['p50_latency_ms']} ms")
    print(f"  Latency p95:       {stats['p95_latency_ms']} ms")
    print()

    # Calls by tool
    print("CALLS BY TOOL (last 30 days)")
    print("-" * 40)
    if stats["calls_by_tool"]:
        for tool, count in stats["calls_by_tool"].items():
            print(f"  {tool:<30} {count:>6}")
    else:
        print("  (no data)")
    print()

    # Calls by client family
    print("CALLS BY CLIENT FAMILY (last 30 days)")
    print("-" * 40)
    if stats["calls_by_client_family"]:
        for family, count in stats["calls_by_client_family"].items():
            print(f"  {family:<30} {count:>6}")
    else:
        print("  (no data)")
    print()

    # Errors by type
    print("ERRORS BY TYPE (last 30 days)")
    print("-" * 40)
    if stats["errors_by_type"]:
        for err_type, count in stats["errors_by_type"].items():
            print(f"  {err_type:<30} {count:>6}")
    else:
        print("  (no errors recorded)")
    print()

    # DB info
    db_path = os.environ.get("FACTRAIL_TELEMETRY_DB", "/var/lib/factrail/telemetry.db")
    retention = int(os.environ.get("FACTRAIL_TELEMETRY_RETENTION_DAYS", "30"))
    print(f"DB: {db_path}")
    print(f"Retention: {retention} days")

    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    """Manually purge old telemetry records."""
    store = get_telemetry_store()
    deleted = store.purge_old()
    db_path = os.environ.get("FACTRAIL_TELEMETRY_DB", "/var/lib/factrail/telemetry.db")
    retention = int(os.environ.get("FACTRAIL_TELEMETRY_RETENTION_DAYS", "30"))
    print(f"DB: {db_path}")
    print(f"Purged {deleted} records older than {retention} days.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="factrail.telemetry",
        description="Factrail privacy-preserving usage telemetry",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("report", help="Show telemetry report")
    subparsers.add_parser("purge", help="Purge old telemetry records")

    args = parser.parse_args()

    if args.command == "report":
        sys.exit(cmd_report(args))
    elif args.command == "purge":
        sys.exit(cmd_purge(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
