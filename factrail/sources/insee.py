"""Source adapter for the INSEE Sirene API v3.11."""

from __future__ import annotations

import logging
import os
import time as _time
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..models import FrenchCompany
from ..normalize import (
    INSEE_API_BASE,
    normalize_siren_response,
    normalize_siret_response,
)
from .bodacc import BodaccAdapter, BodaccUnavailableError
from ..normalize_bodacc import (
    build_events_section,
    build_unavailable_events,
    build_disabled_events,
)
from .rate_limiter import get_rate_limiter, RateLimiterError

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
HTTP_TIMEOUT = 30.0

# Strict BODACC error codes
ERROR_TIMEOUT = "timeout"
ERROR_HTTP_4XX = "http_4xx"
ERROR_HTTP_5XX = "http_5xx"
ERROR_RATE_LIMITED = "rate_limited"
ERROR_MALFORMED_RESPONSE = "malformed_response"
ERROR_NETWORK = "network_error"


class FactrailError(Exception):
    """Base Factrail error."""


class ValidationError(FactrailError):
    pass


class NotFoundError(FactrailError):
    pass


class UpstreamError(FactrailError):
    pass


class RateLimitError(UpstreamError):
    """Raised when rate limit cannot be satisfied."""


class InseeAdapter:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = INSEE_API_BASE,
        timeout: float = HTTP_TIMEOUT,
        max_retries: int = MAX_RETRIES,
        fetch_siege_for_siren: bool = True,
        bodacc_enabled: bool = True,
        bodacc_adapter: Optional[BodaccAdapter] = None,
        rate_limiter=None,
    ) -> None:
        self.api_key = api_key or os.getenv("INSEE_API_KEY", "")
        if not self.api_key:
            logger.warning("INSEE_API_KEY not configured")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.fetch_siege_for_siren = fetch_siege_for_siren
        self.bodacc_enabled = bodacc_enabled
        self.bodacc = bodacc_adapter or BodaccAdapter()
        self.rate_limiter = rate_limiter or get_rate_limiter()
        self._siege_cache: dict[str, tuple[dict, datetime]] = {}
        # Stats tracking
        self.stats = {
            "total_requests": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "rate_limited_count": 0,
            "retry_after_count": 0,
            "total_retries": 0,
        }

    def _headers(self) -> dict:
        return {
            "X-INSEE-Api-Key-Integration": self.api_key,
            "Accept": "application/json",
        }

    def _request(self, path: str) -> Optional[dict]:
        url = f"{self.base_url}{path}"
        last_exc: Optional[Exception] = None

        for attempt in range(self.max_retries):
            # Acquire rate limiter slot
            try:
                wait_time = self.rate_limiter.acquire(timeout=300.0)
                if wait_time > 0:
                    self.stats["rate_limited_count"] += 1
            except RateLimiterError:
                raise RateLimitError("Rate limit exceeded and cannot be satisfied")

            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.get(url, headers=self._headers())
                self.stats["total_requests"] += 1

                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 404:
                    return None
                if resp.status_code == 401:
                    raise UpstreamError("INSEE API key invalid or missing")
                if resp.status_code == 429:
                    # Honor Retry-After if provided
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after:
                        self.rate_limiter.handle_retry_after(retry_after)
                        self.stats["retry_after_count"] += 1
                    else:
                        # Backoff without exceeding reasonable bounds
                        wait = min(2.0 ** (attempt + 1), 8.0)
                        _time.sleep(wait)
                    self.stats["rate_limited_count"] += 1
                    self.stats["total_retries"] += 1
                    continue
                raise UpstreamError(
                    f"Upstream error {resp.status_code}: {resp.text[:200]}"
                )
            except httpx.TimeoutException as exc:
                last_exc = exc
                logger.warning("INSEE timeout on attempt %d", attempt + 1)
                self.stats["total_retries"] += 1
            except httpx.RequestError as exc:
                last_exc = exc
                logger.warning(
                    "INSEE request error on attempt %d", attempt + 1
                )
                self.stats["total_retries"] += 1

        raise UpstreamError(
            f"Upstream unreachable after {self.max_retries} attempts: {last_exc}"
        )

    @staticmethod
    def validate_siren(siren: str) -> bool:
        if len(siren) != 9 or not siren.isdigit():
            return False
        total = 0
        for i, ch in enumerate(reversed(siren)):
            d = int(ch)
            if i % 2 == 1:
                d *= 2
                if d > 9:
                    d -= 9
            total += d
        return total % 10 == 0

    @staticmethod
    def validate_siret(siret: str) -> bool:
        if len(siret) != 14 or not siret.isdigit():
            return False
        total = 0
        for i, ch in enumerate(reversed(siret)):
            d = int(ch)
            if i % 2 == 1:
                d *= 2
                if d > 9:
                    d -= 9
            total += d
        return total % 10 == 0

    @staticmethod
    def classify(identifier: str) -> str:
        cleaned = identifier.strip().replace(" ", "")
        if len(cleaned) == 9 and cleaned.isdigit():
            return "siren"
        if len(cleaned) == 14 and cleaned.isdigit():
            return "siret"
        raise ValidationError(
            f"Identifier must be a 9-digit SIREN or 14-digit SIRET, got: {identifier!r}"
        )

    def lookup(self, identifier: str) -> FrenchCompany:
        kind = self.classify(identifier)
        if kind == "siren" and not self.validate_siren(identifier):
            logger.warning("SIREN failed Luhn check but proceeding")
        if kind == "siret" and not self.validate_siret(identifier):
            logger.warning("SIRET failed Luhn check but proceeding")

        siren_retrieved_at = datetime.now(timezone.utc)
        if kind == "siren":
            raw_siren = self._request(f"/siren/{identifier}")
            if raw_siren is None:
                raise NotFoundError(f"SIREN not found: {identifier}")

            siege_raw = None
            siege_retrieved_at = None
            if self.fetch_siege_for_siren:
                ul = raw_siren.get("uniteLegale", raw_siren)
                latest_period = (ul.get("periodesUniteLegale") or [{}])[0]
                nic_siege = latest_period.get("nicSiegeUniteLegale")
                if nic_siege:
                    siege_siret = f"{identifier}{nic_siege}"
                    if identifier in self._siege_cache:
                        siege_raw, siege_retrieved_at = self._siege_cache[identifier]
                        self.stats["cache_hits"] += 1
                    else:
                        self.stats["cache_misses"] += 1
                        siege_retrieved_at = datetime.now(timezone.utc)
                        siege_raw = self._request(f"/siret/{siege_siret}")
                        if siege_raw:
                            self._siege_cache[identifier] = (siege_raw, siege_retrieved_at)

            return normalize_siren_response(
                raw_siren,
                retrieved_at=siren_retrieved_at,
                siege_raw=siege_raw,
                siege_retrieved_at=siege_retrieved_at,
            )
        else:
            raw_siret = self._request(f"/siret/{identifier}")
            if raw_siret is None:
                raise NotFoundError(f"SIRET not found: {identifier}")
            return normalize_siret_response(raw_siret, retrieved_at=siren_retrieved_at)

    def _resolve_siren(self, identifier: str) -> tuple[str, FrenchCompany]:
        kind = self.classify(identifier)
        company = self.lookup(identifier)
        if kind == "siren":
            return identifier, company
        else:
            return company.siren, company

    def _map_bodacc_error(self, exc: Exception) -> str:
        if isinstance(exc, BodaccUnavailableError):
            msg = str(exc).lower()
            if "timeout" in msg:
                return ERROR_TIMEOUT
            if "429" in msg or "rate" in msg:
                return ERROR_RATE_LIMITED
            if "500" in msg or "503" in msg or "5xx" in msg:
                return ERROR_HTTP_5XX
            if "400" in msg or "404" in msg or "4xx" in msg:
                return ERROR_HTTP_4XX
            if "malformed" in msg or "json" in msg:
                return ERROR_MALFORMED_RESPONSE
            return ERROR_NETWORK
        return ERROR_NETWORK

    def lookup_with_bodacc(self, identifier: str) -> FrenchCompany:
        siren, company = self._resolve_siren(identifier)

        if not self.bodacc_enabled:
            company.events = build_disabled_events(datetime.now(timezone.utc))
            return company

        try:
            result = self.bodacc.search_by_siren(siren)
            events_section = build_events_section(
                result["records"],
                siren,
                result["retrieved_at"],
                result["truncated"],
                result["total"],
            )
            company.events = events_section
        except BodaccUnavailableError as exc:
            error_code = self._map_bodacc_error(exc)
            logger.warning("BODACC unavailable (error=%s)", error_code)
            company.events = build_unavailable_events(
                datetime.now(timezone.utc),
                error=error_code,
            )
        except Exception as exc:
            error_code = self._map_bodacc_error(exc)
            logger.error("Unexpected BODACC error (error=%s)", error_code)
            company.events = build_unavailable_events(
                datetime.now(timezone.utc),
                error=error_code,
            )

        return company

    def search_sirens(
        self,
        etat_admin: str = "A",
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Multicriteria search for SIRENs by administrative state.

        Returns dict with total, sirens list, and has_more flag.
        """
        params = (
            f"periode(etatAdministratifUniteLegale:{etat_admin})"
            f"&nombre={limit}&debut={offset}&champs=siren"
        )
        raw = self._request(f"/siren?q={params}")
        if raw is None:
            return {"total": 0, "sirens": [], "has_more": False}
        total = raw.get("header", {}).get("total", 0)
        sirens = [ul["siren"] for ul in raw.get("unitesLegales", [])]
        has_more = offset + limit < total
        return {"total": total, "sirens": sirens, "has_more": has_more}
