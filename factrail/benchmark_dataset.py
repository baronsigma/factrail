"""Generate a verified benchmark dataset from authoritative INSEE data.

Uses INSEE multicriteria search to pull real SIRENs/SIRETs.
No manual identifier invention.
"""

from __future__ import annotations

import json
import logging
import os
import time as _time
from typing import Optional

from .sources.insee import InseeAdapter
from .sources.rate_limiter import get_rate_limiter

logger = logging.getLogger(__name__)


def generate_benchmark_dataset(
    api_key: Optional[str] = None,
    target_sirens: int = 500,
    target_sirets: int = 100,
    output_path: str = "benchmark_dataset.json",
) -> dict:
    """Generate a verified dataset from INSEE multicriteria queries.

    Strategy:
    1. Pull active SIRENs (etatAdministratif:A) — covers ~25M entities
    2. Pull inactive SIRENs (etatAdministratif:C) — covers ~14M entities
    3. For a subset, resolve their siege SIRET
    """
    adapter = InseeAdapter(api_key=api_key)
    limiter = get_rate_limiter()

    sirens = []
    sirets = []

    # Pull active SIRENs
    logger.info("Pulling active SIRENs...")
    active_sirens = _pull_sirens_by_state(adapter, "A", target_sirens // 2)
    sirens.extend(active_sirens)

    # Pull inactive SIRENs
    logger.info("Pulling inactive SIRENs...")
    inactive_sirens = _pull_sirens_by_state(adapter, "C", target_sirens - len(sirens))
    sirens.extend(inactive_sirens)

    # Resolve SIRETs for a subset
    logger.info("Resolving SIRETs for subset...")
    siret_count = 0
    for siren in sirens[:target_sirets * 2]:  # try more than needed
        if siret_count >= target_sirets:
            break
        try:
            raw = adapter._request(f"/siren/{siren}")
            if raw:
                ul = raw.get("uniteLegale", raw)
                latest = (ul.get("periodesUniteLegale") or [{}])[0]
                nic = latest.get("nicSiegeUniteLegale")
                if nic:
                    sirets.append(f"{siren}{nic}")
                    siret_count += 1
        except Exception as e:
            logger.warning("Failed to resolve SIRET for %s: %s", siren, e)

    dataset = {
        "metadata": {
            "generated_at": __import__("datetime").datetime.now().isoformat(),
            "source": "INSEE Sirene API v3.11 (multicriteria search)",
            "total_sirens": len(sirens),
            "total_sirets": len(sirets),
        },
        "sirens": sirens,
        "sirets": sirets,
    }

    with open(output_path, "w") as f:
        json.dump(dataset, f, indent=2)

    logger.info(
        "Dataset generated: %d SIRENs, %d SIRETs -> %s",
        len(sirens), len(sirets), output_path
    )
    return dataset


def _pull_sirens_by_state(adapter: InseeAdapter, state: str, count: int) -> list:
    """Pull SIRENs by administrative state using multicriteria search."""
    sirens = []
    offset = 0
    batch_size = 50

    while len(sirens) < count:
        try:
            result = adapter.search_sirens(etat_admin=state, limit=batch_size, offset=offset)
            batch = result["sirens"]
            if not batch:
                break
            sirens.extend(batch)
            if not result["has_more"]:
                break
            offset += batch_size
        except Exception as e:
            logger.warning("Failed to pull batch at offset %d: %s", offset, e)
            break

    return sirens[:count]


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    api_key = os.getenv("INSEE_API_KEY", "")
    if not api_key:
        print("INSEE_API_KEY required")
        sys.exit(1)
    generate_benchmark_dataset(api_key=api_key)
