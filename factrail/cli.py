"""Simple CLI for Factrail."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .sources.insee import InseeAdapter


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="factrail",
        description="Verified facts for agents",
    )
    parser.add_argument("identifier", help="SIREN (9 digits) or SIRET (14 digits)")
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose logging")
    parser.add_argument("--no-bodacc", action="store_true", help="skip BODACC event lookup")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    adapter = InseeAdapter(bodacc_enabled=not args.no_bodacc)
    if args.no_bodacc:
        result = adapter.lookup(args.identifier)
    else:
        result = adapter.lookup_with_bodacc(args.identifier)
    out = result.model_dump(mode="json")
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
