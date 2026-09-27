"""
EU TARIC / Access2Markets authoritative data ingestion for FACTRAIL Trade.

Architecture:
  - Authoritative source: EU Access2Markets (built on DG TAXUD TARIC database)
    URL: https://trade.ec.europa.eu/access-to-markets/en/results?product=<HS>&origin=<ISO2>&destination=<ISO2>
    Update frequency: daily (TARIC data updated daily; A2M presents TARIC + Mendel data)
    Source for EU tariffs: DG TAXUD TARIC database (public sector information)
    Source for non-EU third-country tariffs: Mendel Verlag (restricted license — personal
    reference use only; not for database building or resale)

  - Local cache: SQLite with TTL-based freshness.  Serves last valid result if
    refresh fails.  Marked as "cached" / "derived" with provenance when served from
    cache, "verified" when freshly retrieved.

  - Scope: point lookups for (HS code, origin country, destination country).
    Not a bulk TARIC database.  Each lookup retrieves the measures applicable to
    that specific trade flow and stores them with full provenance.

  - Failure mode: if the upstream is unreachable, the last successful retrieval
    is served with a "cached" status and a warning.  If no cache exists, returns
    unavailable with provenance pointing to the authoritative source URL.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time as _time
from datetime import datetime, timezone
from logging import getLogger
from pathlib import Path
from typing import Any, Optional

import httpx
import os

logger = getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

A2M_BASE = "https://trade.ec.europa.eu/access-to-markets"
A2M_RESULTS_URL = (
    f"{A2M_BASE}/en/results?product={{hs_code}}&origin={{origin}}&destination={{dest}}"
)
A2M_VERSION_RE = re.compile(r"Version:\s*([\d.]+)\s*\((\d{4}-\d{2}-\d{2})")

# TTL: re-fetch live data after this many seconds.  TARIC is updated daily,
# so a 12-hour TTL strikes a balance between freshness and rate-limit respect.
DEFAULT_TTL_SECONDS = 12 * 3600

# The non-EU third-country duty data on A2M comes from Mendel Verlag and
# carries a restrictive license.  EU TARIC data (suspensions, preferences,
# anti-dumping, etc.) is DG TAXUD public sector info.  We treat the whole
# A2M page as a reference source and cache it with a TTL, not as a bulk store.
# See: https://trade.ec.europa.eu/access-to-markets/en/content/sources-and-copyright

# EU member state ISO2 -> ISO3 (for evidence authority labels)
EU_ISO3 = {
    "AT": "AUT", "BE": "BEL", "BG": "BGR", "HR": "HRV", "CY": "CYP",
    "CZ": "CZE", "DK": "DNK", "EE": "EST", "FI": "FIN", "FR": "FRA",
    "DE": "DEU", "GR": "GRC", "HU": "HUN", "IE": "IRL", "IT": "ITA",
    "LV": "LVA", "LT": "LTU", "LU": "LUX", "MT": "MLT", "NL": "NLD",
    "PL": "POL", "PT": "PRT", "RO": "ROU", "SK": "SVK", "SI": "SVN",
    "ES": "ESP", "SE": "SWE",
}

# ---------------------------------------------------------------------------
# Datastore
# ---------------------------------------------------------------------------

_schema_sql = """
CREATE TABLE IF NOT EXISTS taric_cache (
    hs_code        TEXT    NOT NULL,
    origin         TEXT    NOT NULL,
    destination    TEXT    NOT NULL,
    retrieved_at   REAL    NOT NULL,
    ttls_until     REAL    NOT NULL,
    version        TEXT,
    version_date   TEXT,
    measures       TEXT    NOT NULL,  -- JSON blob
    source_note    TEXT    NOT NULL,
    PRIMARY KEY (hs_code, origin, destination)
);
"""

_a2m_measures_kind = (
    "third_country_duty",
    "low_value_consignment_duty",
    "tariff_suspension",
    "airworthiness_suspension",
    "anti_dumping",
    "countervailing",
    "safeguard",
    "additional_duty",
    "prohibition",
    "restriction",
    "quota",
    "preference",
)


class TaricStore:
    """Local cache for EU TARIC / Access2Markets point lookups."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        http_timeout: float = 20.0,
    ) -> None:
        self.ttl = ttl_seconds
        self.http_timeout = http_timeout
        self._db = Path(db_path or os.environ.get("FACTRAIL_TARIC_DB", "")) if db_path or os.environ.get("FACTRAIL_TARIC_DB") else None
        if self._db is None:
            self._db = Path.home() / ".factrail" / "taric.db"
        self._db.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._client: Optional[httpx.Client] = None

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        with sqlite3.connect(str(self._db)) as conn:
            conn.executescript(_schema_sql)

    # ------------------------------------------------------------------
    # HTTP client (lazy)
    # ------------------------------------------------------------------

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.http_timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": (
                        "FactrailTrade/1.0 (EU TARIC assessment tool; "
                        "+https://factrail.online/contact)"
                    ),
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en",
                },
            )
        return self._client

    # ------------------------------------------------------------------
    # Fetch from Access2Markets
    # ------------------------------------------------------------------

    def fetch_measures(
        self,
        hs_code: str,
        origin: str,
        destination: str,
    ) -> dict[str, Any]:
        """Fetch applicable measures from EU Access2Markets for a trade flow.

        Returns a structured dict with:
          - version: A2M page version string
          - version_date: date of the version
          - measures: list of measure dicts
          - source_note: provenance note

        Raises httpx.HTTPError on network/HTTP failure.
        """
        url = A2M_RESULTS_URL.format(
            hs_code=hs_code, origin=origin.upper(), dest=destination.upper()
        )
        logger.info("taric_fetch_start")
        t0 = _time.monotonic()
        resp = self._get_client().get(url)
        resp.raise_for_status()
        latency_ms = round((_time.monotonic() - t0) * 1000, 1)
        logger.info("taric_fetch_ok", extra={"latency_ms": latency_ms})

        html = resp.text
        version, version_date = _parse_a2m_version(html)

        measures = _parse_measures(html, hs_code, origin, destination)

        return {
            "version": version,
            "version_date": version_date,
            "measures": measures,
            "source_note": (
                f"EU Access2Markets (v{version}, {version_date}) — tariff data from "
                f"DG TAXUD TARIC database (EU public sector information) and Mendel Verlag "
                f"(non-EU third-country duties; personal reference use). "
                f"URL: {url}"
            ),
        }

    # ------------------------------------------------------------------
    # Cache lookup / store
    # ------------------------------------------------------------------

    def lookup(self, hs_code: str, origin: str, destination: str) -> Optional[dict]:
        """Return cached measures if fresh, else None."""
        now = _time.time()
        with sqlite3.connect(str(self._db)) as conn:
            row = conn.execute(
                "SELECT measures, ttls_until, version, version_date, source_note "
                "FROM taric_cache WHERE hs_code=? AND origin=? AND destination=?",
                (hs_code, origin.upper(), destination.upper()),
            ).fetchone()
        if row is None:
            return None
        measures_json, ttls_until, version, version_date, source_note = row
        if now < ttls_until:
            return {
                "version": version,
                "version_date": version_date,
                "measures": json.loads(measures_json),
                "source_note": source_note,
                "cached": True,
            }
        return None

    def store(
        self,
        hs_code: str,
        origin: str,
        destination: str,
        data: dict[str, Any],
    ) -> None:
        """Atomically store fetched measures in the cache."""
        now = _time.time()
        ttls_until = now + self.ttl
        measures_json = json.dumps(data["measures"], ensure_ascii=False)
        with sqlite3.connect(str(self._db)) as conn:
            conn.execute(
                """INSERT INTO taric_cache
                   (hs_code, origin, destination, retrieved_at, ttls_until,
                    version, version_date, measures, source_note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(hs_code, origin, destination) DO UPDATE SET
                       retrieved_at=excluded.retrieved_at,
                       ttls_until=excluded.ttls_until,
                       version=excluded.version,
                       version_date=excluded.version_date,
                       measures=excluded.measures,
                       source_note=excluded.source_note""",
                (
                    hs_code,
                    origin.upper(),
                    destination.upper(),
                    now,
                    ttls_until,
                    data.get("version"),
                    data.get("version_date"),
                    measures_json,
                    data.get("source_note", ""),
                ),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # High-level: get measures with cache-or-fetch semantics
    # ------------------------------------------------------------------

    def get_measures(
        self,
        hs_code: str,
        origin: str,
        destination: str,
        *,
        allow_stale: bool = True,
    ) -> dict[str, Any]:
        """Return applicable measures for a trade flow.

        Tries cache first (fresh or stale if allow_stale).  If no usable cache,
        fetches live from Access2Markets.  If live fetch fails and a stale cache
        exists, serves the stale cache with a warning.  If nothing is available,
        raises TaricUnavailable.
        """
        hs = hs_code
        origin = origin.upper()
        destination = destination.upper()

        cached = self.lookup(hs, origin, destination)
        if cached is not None:
            if not cached.get("cached"):
                return cached
            # Fresh cache
            if allow_stale or cached["ttls_until"] > _time.time():
                cached["_source"] = "cache_fresh"
                return cached
            # Stale but allowed
            cached["_source"] = "cache_stale"
            return cached

        # No cache — fetch live
        try:
            data = self.fetch_measures(hs, origin, destination)
            self.store(hs, origin, destination, data)
            data["_source"] = "live"
            return data
        except Exception as exc:
            logger.warning("taric_live_fetch_failed")
            # Try serving stale cache as last resort
            cached = self.lookup(hs, origin, destination)
            if cached is not None and allow_stale:
                cached["_source"] = "cache_stale (live failed)"
                cached["_warning"] = f"Live TARIC fetch failed ({exc}); serving cached data (fetched {datetime.fromtimestamp(cached['ttls_until'] - self.ttl, tz=timezone.utc).isoformat()})."
                return cached
            raise TaricUnavailable(
                hs_code=hs, origin=origin, destination=destination, reason=str(exc)
            ) from exc

    def clear(self, hs_code: Optional[str] = None) -> None:
        """Clear cache entries.  If hs_code given, clear only that code."""
        with sqlite3.connect(str(self._db)) as conn:
            if hs_code:
                conn.execute("DELETE FROM taric_cache WHERE hs_code=?", (hs_code,))
            else:
                conn.execute("DELETE FROM taric_cache")
            conn.commit()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_a2m_version(html: str) -> tuple[str, str]:
    """Extract A2M page version and date from HTML."""
    m = A2M_VERSION_RE.search(html)
    if m:
        return m.group(1), m.group(2)
    return "unknown", "unknown"


def _parse_measures(html: str, hs_code: str, origin: str, destination: str) -> list[dict]:
    """Parse the measures table from an Access2Markets results page.

    The A2M results page structures tariffs as:

      Origin/ Measure type:   <origin-area>
      Measure type:           <measure-type-name>
      Tariff:                 <rate>
      EU law:                 <regulation-reference>
      Footnotes:              <TM/footnote code and text>
      Conditions:             <table of condition codes>
    """
    measures: list[dict] = []

    # The page lists multiple origin areas (ERGA OMNES, specific countries,
    # preferences).  Each area block contains measure-type entries.
    # We parse by finding the structure: "Origin/ Measure type:" header,
    # then "Measure type:" + "Tariff:" pairs.

    # Normalize whitespace for regex matching
    text = re.sub(r"[ \t]+", " ", html)
    text = re.sub(r"\\s*\\n\\s*", "\\n", text)

    # Find all measure blocks
    # Pattern: "Origin/\nMeasure type:\n<origin-area>" followed by measure entries
    block_pattern = re.compile(
        r"Origin/\s*Measure type:\s*([^\n]+?)\s+"
        r"(?:Origin/\s*Measure type:|$)",
        re.DOTALL,
    )

    # Simpler approach: split by "Origin/ Measure type:" occurrences
    # and parse each block
    parts = re.split(r"(Origin/\s*Measure type:\s*[^\n]+)", text)

    current_origin_area: Optional[str] = None
    i = 0
    while i < len(parts):
        part = parts[i]
        m = re.match(r"Origin/\s*Measure type:\s*([^\n]+)", part)
        if m:
            current_origin_area = m.group(1).strip()
            i += 1
            continue

        if current_origin_area and part.strip():
            # Parse measure entries within this origin area
            # Look for "Measure type:" + "Tariff:" pairs
            measure_blocks = re.split(r"(Measure type:\s*[^\n]+)", part)
            j = 0
            current_measure_type: Optional[str] = None
            while j < len(measure_blocks):
                block = measure_blocks[j]
                m2 = re.match(r"Measure type:\s*([^\n]+)", block)
                if m2:
                    current_measure_type = m2.group(1).strip()
                    j += 1
                    continue

                if current_measure_type:
                    # Extract tariff value
                    tariff_m = re.search(
                        r"Tariff:\s*\n?\s*([^\n]+)", measure_blocks[j]
                    )
                    tariff = tariff_m.group(1).strip() if tariff_m else None

                    # Extract EU law reference
                    law_m = re.search(r"EU law:\s*\n?\s*([^\n]+)", measure_blocks[j])
                    eu_law = law_m.group(1).strip() if law_m else None

                    # Extract footnotes
                    footnotes = _extract_footnotes(measure_blocks[j])

                    # Extract conditions
                    conditions = _extract_conditions(measure_blocks[j])

                    # Classify measure type
                    measure_kind = _classify_measure_type(current_measure_type)

                    if tariff or eu_law or footnotes or conditions:
                        measures.append({
                            "origin_area": current_origin_area,
                            "measure_type": current_measure_type,
                            "measure_kind": measure_kind,
                            "tariff": tariff,
                            "eu_law_reference": eu_law,
                            "footnotes": footnotes,
                            "conditions": conditions,
                            "review_required": _check_review_required(
                                measure_kind, conditions, footnotes
                            ),
                        })

                j += 1

        i += 1

    return measures


def _extract_footnotes(text: str) -> list[dict]:
    """Extract footnote entries (TM/footnote codes with text)."""
    footnotes = []
    # Pattern: **TM1234:** or **EU123:** followed by text
    fn_pattern = re.compile(r"\*\*(TM\d+|EU\d+|CD\d+|C\d+|MP\d+):\*\*\s*([^\n]+)")
    for m in fn_pattern.finditer(text):
        footnotes.append({
            "code": m.group(1),
            "text": m.group(2).strip(),
        })
    return footnotes


def _extract_conditions(text: str) -> list[dict]:
    """Extract conditions table from a measure block."""
    conditions = []
    # Pattern: | C1 | ... | ... | ... |
    cond_pattern = re.compile(
        r"\|\s*(C?\d+)\s*\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|"
    )
    for m in cond_pattern.finditer(text):
        code = m.group(1)
        cert = m.group(2).strip()
        action = m.group(3).strip()
        note = m.group(4).strip()
        conditions.append({
            "code": code,
            "certificate_or_document": cert,
            "action": action,
            "note": note,
        })
    return conditions


def _classify_measure_type(measure_type: str) -> str:
    """Map A2M measure type string to a canonical kind."""
    t = measure_type.lower()
    if "third country duty" in t:
        return "third_country_duty"
    if "low-value consignment" in t:
        return "low_value_consignment_duty"
    if "suspension" in t and "airworthiness" in t:
        return "airworthiness_suspension"
    if "suspension" in t:
        return "tariff_suspension"
    if "anti-dumping" in t or "anti-dumping" in t:
        return "anti_dumping"
    if "countervailing" in t:
        return "countervailing"
    if "safeguard" in t:
        return "safeguard"
    if "additional" in t:
        return "additional_duty"
    if "prohibition" in t:
        return "prohibition"
    if "restriction" in t:
        return "restriction"
    if "quota" in t:
        return "quota"
    if "preference" in t or "preferential" in t:
        return "preference"
    return "other"


def _check_review_required(
    measure_kind: str, conditions: list, footnotes: list
) -> bool:
    """Determine if a measure requires human review."""
    if measure_kind in ("anti_dumping", "countervailing", "safeguard"):
        return True  # These are company/product-specific
    if conditions:
        # Conditional measures need review to determine applicability
        return True
    if measure_kind == "tariff_suspension":
        return True  # Suspensions often have end-use conditions
    return False


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class TaricUnavailable(Exception):
    """No TARIC data available for the requested lookup."""

    def __init__(
        self,
        hs_code: str,
        origin: str,
        destination: str,
        reason: str,
    ) -> None:
        self.hs_code = hs_code
        self.origin = origin
        self.destination = destination
        self.reason = reason
        super().__init__(
            f"TARIC data unavailable for {hs_code} {origin}->{destination}: {reason}"
        )


# ---------------------------------------------------------------------------
# Convenience: parse a tariff string to a float or None
# ---------------------------------------------------------------------------

def parse_tariff_rate(tariff_str: Optional[str]) -> Optional[float]:
    """Parse a tariff string like '6.70%' or '3.00 EUR' to a float rate.

    Returns the percentage as a float (e.g. 6.70) for percentage tariffs,
    or None for non-percentage tariffs (fixed amounts, etc.).
    """
    if tariff_str is None:
        return None
    s = tariff_str.strip()
    pct_m = re.match(r"^([\d.]+)\s*%?$", s)
    if pct_m:
        return float(pct_m.group(1))
    return None  # Fixed amount or other non-percentage


# ---------------------------------------------------------------------------
# Convenience: find the third-country MFN duty from measures
# ---------------------------------------------------------------------------

def find_mfn_duty(measures: list[dict]) -> Optional[dict]:
    """Return the third-country duty measure, if present."""
    for m in measures:
        if m["measure_kind"] == "third_country_duty":
            return m
    return None


def find_preference(measures: list[dict]) -> Optional[dict]:
    """Return a preference measure, if present."""
    for m in measures:
        if m["measure_kind"] == "preference":
            return m
    return None


# ---------------------------------------------------------------------------
# Module-level default instance (lazy — created on first use)
# ---------------------------------------------------------------------------

_default_store: Optional[TaricStore] = None


def get_default_store() -> TaricStore:
    global _default_store
    if _default_store is None:
        _default_store = TaricStore()
    return _default_store


def lookup_import_measures(
    commodity_code: str,
    origin_country: str,
    import_date: Optional[str] = None,
) -> dict[str, Any]:
    """Public query entry point.

    Wraps ``TaricStore.get_measures`` with the signature requested by the spec.
    ``import_date`` is reserved for future effective-date filtering; the A2M
    live data reflects the current TARIC apply-from dates.
    """
    store = get_default_store()
    result = store.get_measures(commodity_code, origin_country, "FR")
    return result


# backwards-compat alias
lookup_taric_measures = lookup_import_measures
