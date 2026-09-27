"""Production benchmark runner with rate-pacing."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import statistics
import time as _time
from datetime import datetime, timezone
from typing import Optional

from .models import FrenchCompany
from .sources.insee import InseeAdapter, NotFoundError, RateLimitError, UpstreamError
from .sources.rate_limiter import get_rate_limiter

logger = logging.getLogger(__name__)


def run_benchmark(
    api_key: str,
    dataset_path: str = "benchmark_dataset.json",
) -> dict:
    """Run benchmark against verified dataset, respecting rate limits."""
    with open(dataset_path) as f:
        dataset = json.load(f)

    adapter = InseeAdapter(api_key=api_key)

    all_ids = dataset["sirens"] + dataset["sirets"]
    total = len(all_ids)

    outcomes = {
        "success": 0,
        "not_found": 0,
        "partial_diffusion": 0,
        "rate_limited": 0,
        "upstream_error": 0,
        "normalization_error": 0,
        "unexpected_error": 0,
    }

    results = []
    latencies = []
    bodacc_truncations = 0
    bodacc_total = 0
    start_time = _time.monotonic()

    for i, identifier in enumerate(all_ids):
        if (i + 1) % 10 == 0:
            elapsed = _time.monotonic() - start_time
            logger.info(f"Progress: {i+1}/{total} ({elapsed:.0f}s)")

        result = {"identifier": identifier, "outcome": None, "latency_s": 0}
        start = _time.monotonic()

        try:
            company = adapter.lookup_with_bodacc(identifier)
            elapsed = _time.monotonic() - start
            latencies.append(elapsed)

            try:
                dump = company.model_dump(mode="json")
                assert "siren" in dump
                assert "checked_at" in dump
                assert "events" in dump

                if company.diffusion_status == "diffusion_partielle":
                    outcomes["partial_diffusion"] += 1
                    result["outcome"] = "partial_diffusion"
                else:
                    outcomes["success"] += 1
                    result["outcome"] = "success"

                if company.events and company.events.get("source_status") == "available":
                    bodacc_total += 1
                    if company.events.get("truncated"):
                        bodacc_truncations += 1

            except Exception as e:
                outcomes["normalization_error"] += 1
                result["outcome"] = "normalization_error"
                result["error"] = str(e)

        except NotFoundError as e:
            outcomes["not_found"] += 1
            result["outcome"] = "not_found"
        except RateLimitError as e:
            outcomes["rate_limited"] += 1
            result["outcome"] = "rate_limited"
        except UpstreamError as e:
            outcomes["upstream_error"] += 1
            result["outcome"] = "upstream_error"
            result["error"] = str(e)
        except Exception as e:
            outcomes["unexpected_error"] += 1
            result["outcome"] = "unexpected_error"
            result["error"] = str(e)

        result["latency_s"] = round(_time.monotonic() - start, 3)
        results.append(result)

    elapsed_total = _time.monotonic() - start_time

    sorted_latencies = sorted(latencies) if latencies else []
    p50 = sorted_latencies[int(len(sorted_latencies) * 0.5)] if sorted_latencies else None
    p95 = sorted_latencies[int(len(sorted_latencies) * 0.95)] if sorted_latencies else None
    p99 = sorted_latencies[int(len(sorted_latencies) * 0.99)] if sorted_latencies else None

    cache_hits = adapter.stats["cache_hits"]
    cache_misses = adapter.stats["cache_misses"]
    total_cache = cache_hits + cache_misses

    return {
        "metadata": {
            "dataset": dataset_path,
            "dataset_size": total,
            "sirens": len(dataset["sirens"]),
            "sirets": len(dataset["sirets"]),
            "run_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_total_s": round(elapsed_total, 1),
        },
        "outcomes": outcomes,
        "latency": {
            "p50_ms": round(p50 * 1000, 1) if p50 else None,
            "p95_ms": round(p95 * 1000, 1) if p95 else None,
            "p99_ms": round(p99 * 1000, 1) if p99 else None,
            "max_ms": round(max(latencies) * 1000, 1) if latencies else None,
        },
        "insee_requests": {
            "total": adapter.stats["total_requests"],
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "cache_hit_rate_pct": round(cache_hits / total_cache * 100, 1) if total_cache else 0,
            "rate_limited": adapter.stats["rate_limited_count"],
            "retries": adapter.stats["total_retries"],
            "avg_requests_per_lookup": round(adapter.stats["total_requests"] / total, 2) if total else 0,
        },
        "bodacc": {
            "total_lookups": bodacc_total,
            "truncated": bodacc_truncations,
            "truncation_rate_pct": round(bodacc_truncations / bodacc_total * 100, 1) if bodacc_total else 0,
        },
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(prog="factrail-benchmark")
    parser.add_argument("--key", help="INSEE API key")
    parser.add_argument("--dataset", default="benchmark_dataset.json")
    args = parser.parse_args()

    api_key = args.key or os.getenv("INSEE_API_KEY", "")
    if not api_key:
        print("INSEE_API_KEY required")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    print(f"Running benchmark on {args.dataset}...")
    result = run_benchmark(api_key=api_key, dataset_path=args.dataset)
    print(json.dumps(result, indent=2, ensure_ascii=False))

    outcomes = result["outcomes"]
    total = result["metadata"]["dataset_size"]
    success = outcomes["success"] + outcomes["partial_diffusion"]
    print(f"\n{'='*60}")
    print(f"Total: {total}")
    print(f"Success: {success} ({round(success/total*100,1)}%)")
    print(f"Not found: {outcomes['not_found']}")
    print(f"Rate limited: {outcomes['rate_limited']}")
    print(f"Upstream errors: {outcomes['upstream_error']}")
    print(f"Normalization errors: {outcomes['normalization_error']}")
    print(f"Unexpected errors: {outcomes['unexpected_error']}")
    lat = result["latency"]
    if lat["p50_ms"]:
        print(f"Latency: p50={lat['p50_ms']}ms p95={lat['p95_ms']}ms p99={lat['p99_ms']}ms")


if __name__ == "__main__":
    main()
