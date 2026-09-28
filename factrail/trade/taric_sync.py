"""Manual TARIC snapshot ingestion and status command."""
from __future__ import annotations

import argparse
import json
import sys

from .official_taric_store import OfficialTaricStore, TaricSnapshotError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m factrail.trade.taric_sync")
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest", help="validate and activate an official Commission TARIC Excel snapshot")
    ingest.add_argument("path")
    ingest.add_argument("--reference-date", required=True, help="effective/reference date YYYY-MM-DD stated by the official extraction")
    ingest.add_argument("--partial", action="store_true", help="explicitly allow a reduced table set; results are marked partial")
    ingest.add_argument("--store-dir")
    status = sub.add_parser("status", help="show active and last-good official snapshot metadata")
    status.add_argument("--store-dir")
    fetch = sub.add_parser("fetch", help="official automatic acquisition (not currently available)")
    fetch.add_argument("--store-dir")
    args = parser.parse_args(argv)
    store = OfficialTaricStore(args.store_dir)
    try:
        if args.command == "ingest":
            result = store.ingest(args.path, reference_date=args.reference_date, partial=args.partial)
        elif args.command == "status":
            result = store.status()
        else:
            result = {"source": "EU_TARIC", "source_outcome": "source_unavailable",
                      "reason": "No stable direct Commission/CIRCABC file-download endpoint or manifest was verified. Supply an official XLSX/ZIP snapshot with the ingest command."}
            print(json.dumps(result, indent=2, sort_keys=True))
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (TaricSnapshotError, OSError, ValueError) as exc:
        print(json.dumps({"error": "snapshot_rejected", "detail": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
