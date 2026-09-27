"""BODACC open-data adapter (DILA / opendatasoft v2.0).

Dataset: annonces-commerciales
Search via multi-valued `registre` field (contains SIREN + SIRET variants).
Auth: none (public open-data endpoint).
"""

from __future__ import annotations

import logging
import time as _time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

BODACC_API_BASE = "https://bodacc-datadila.opendatasoft.com/api/explore/v2.0"
BODACC_DATASET = "annonces-commerciales"
BODACC_API_NAME = "BODACC Annonces Commerciales (DILA)"

MAX_RETRIES = 3
BACKOFF_BASE = 1.5
HTTP_TIMEOUT = 30.0
MAX_LIMIT = 50  # sane page cap; ODSoft max is typically 10000 but we don't need that


class BodaccError(Exception):
    pass


class BodaccUnavailableError(BodaccError):
    pass


class BodaccAdapter:
    def __init__(
        self,
        base_url: str = BODACC_API_BASE,
        dataset: str = BODACC_DATASET,
        timeout: float = HTTP_TIMEOUT,
        max_retries: int = MAX_RETRIES,
        max_limit: int = MAX_LIMIT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.dataset = dataset
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_limit = max_limit
        # Simple in-memory cache: (siren, limit) -> (result, timestamp)
        self._cache: dict[str, tuple[dict, datetime]] = {}

    def _url(self, **params: Any) -> str:
        qs = urlencode(params, doseq=False)
        return f"{self.base_url}/catalog/datasets/{self.dataset}/records?{qs}"

    def _request(self, **params: Any) -> Optional[dict]:
        """GET with retries. Returns parsed JSON or None on 404/0 results."""
        url = self._url(**params)
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.get(url)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in (404, 400):
                    return None
                if resp.status_code == 429:
                    wait = BACKOFF_BASE ** (attempt + 1)
                    logger.warning("BODACC rate-limited, retrying in %.1fs", wait)
                    _time.sleep(wait)
                    continue
                raise BodaccUnavailableError(
                    f"BODACC upstream error {resp.status_code}: {resp.text[:200]}"
                )
            except httpx.TimeoutException as exc:
                last_exc = exc
                logger.warning("BODACC timeout on attempt %d", attempt + 1)
            except httpx.RequestError as exc:
                last_exc = exc
                logger.warning("BODACC request error on attempt %d", attempt + 1)

        raise BodaccUnavailableError(
            f"BODACC unreachable after {self.max_retries} attempts: {last_exc}"
        )

    def search_by_siren(self, siren: str, limit: int = MAX_LIMIT) -> dict:
        """Search BODACC by SIREN. Returns dict with records, total, truncated.

        Result shape:
          {"total": N, "records": [...], "truncated": bool, "retrieved_at": datetime}
        """
        # Clamp limit to our max
        limit = min(limit, self.max_limit)
        cache_key = f"siren:{siren}:{limit}"
        if cache_key in self._cache:
            result, ts = self._cache[cache_key]
            return {**result, "retrieved_at": ts, "cached": True}

        retrieved_at = datetime.now(timezone.utc)
        raw = self._request(
            where=f'registre LIKE "%{siren}%"',
            sort="-dateparution",
            limit=str(limit),
            offset="0",
            include_app_metas="False",
            include_links="False",
        )

        if raw is None:
            result = {
                "total": 0,
                "records": [],
                "truncated": False,
                "retrieved_at": retrieved_at,
            }
        else:
            total = raw.get("total_count", 0)
            records = [r["record"]["fields"] for r in raw.get("records", [])]
            result = {
                "total": total,
                "records": records,
                "truncated": total > limit,
                "retrieved_at": retrieved_at,
            }

        self._cache[cache_key] = (result, retrieved_at)
        return result
