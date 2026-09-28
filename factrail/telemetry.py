"""Factrail V1.3.4 — Privacy-preserving MCP usage telemetry.

Measures real tool usage at the MCP tools/call execution layer.
Stores only coarse, non-identifying metadata — never raw IPs, raw
User-Agent strings, tool arguments, Authorization headers, cookies,
API keys, or SIREN/SIRET values.

All writes are fire-and-forget and wrapped in try/except so that
telemetry failures can never affect MCP request processing.

Environment loading (CLI + server)
---------------------------------

``_load_project_env()`` reads ``.env`` from the project root (the
directory above the ``factrail`` package) when present and the
variables are not already set.  This lets ``python3 -m
factrail.telemetry report`` work from a fresh shell without manually
sourcing ``.env``.

FACTRAIL_TELEMETRY_SECRET is never printed, logged, or otherwise
exposed by this module.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import sqlite3
import threading
import time as _time
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2

# ---------------------------------------------------------------------------
# Project .env auto-loading for CLI use
# ---------------------------------------------------------------------------

def _project_root() -> str:
    """Return the project root directory (parent of the ``factrail`` package).

    Works both in editable installs (``/root/factrail``) and in any other
    location where ``factrail`` is installed as a package — the project root
    is defined as the grandparent of this module's file (the ``factrail/``
    package dir is the child).
    """
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _load_project_env() -> None:
    """Load variables from ``<project_root>/.env`` into ``os.environ``.

    Only sets variables that are *not* already present in the environment,
    so a systemd service that already loaded them (via EnvironmentFile) is
    unaffected.  ``FACTRAIL_TELEMETRY_SECRET`` is loaded but never printed,
    logged, or otherwise exposed by this module.

    Safe to call multiple times — idempotent.
    """
    dotenv = os.path.join(_project_root(), ".env")
    if not os.path.isfile(dotenv):
        return
    try:
        with open(dotenv, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if not key or key in os.environ:
                    continue
                os.environ[key] = value
    except Exception:
        pass  # A bad .env must never break the CLI or the server


# Call once at import time so both ``factrail.telemetry`` (server path) and
# ``python3 -m factrail.telemetry`` (CLI path) get the project env.
_load_project_env()


_CLIENT_FAMILIES = {"goose": "goose", "claude": "claude", "cursor": "cursor", "inspector": "inspector"}


def _get_secret() -> str:
    """Return the telemetry secret from env or a per-install fallback."""
    return os.environ.get("FACTRAIL_TELEMETRY_SECRET", "factrail-default-telemetry-secret-change-me")


def _detect_client_family(user_agent: str) -> str:
    """Coarsely detect client family from User-Agent string.

    Returns one of: goose, claude, cursor, inspector, unknown.
    Never stores the raw User-Agent.
    """
    token = user_agent.strip().lower().split("/", 1)[0]
    return _CLIENT_FAMILIES.get(token, "unknown")


def _derive_daily_client_hash(
    client_ip: str,
    client_family: str,
    utc_date: str,
    secret: str,
) -> str:
    """Derive an anonymous daily client identifier.

    HMAC-SHA256 over (client_ip + client_family + utc_date + secret).
    Rotates naturally every day because utc_date is an input.
    Never persists the raw IP or raw User-Agent.
    """
    message = f"{client_ip}|{client_family}|{utc_date}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()[:16]


def _get_client_ip(request: Any) -> str:
    """Extract client IP, respecting Cloudflare CF-Connecting-IP.

    Only trusts CF-Connecting-IP when the request actually came through
    Cloudflare (detected via CF-RAY header presence).
    """
    headers = request.headers
    # If CF-RAY is present, the request came through Cloudflare
    if headers.get("cf-ray"):
        cf_ip = headers.get("cf-connecting-ip")
        if cf_ip:
            return cf_ip
    # Fall back to direct connection IP
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


class TelemetryStore:
    """SQLite-backed telemetry storage with automatic retention pruning."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        retention_days: int = 30,
    ) -> None:
        self.db_path = db_path or os.environ.get(
            "FACTRAIL_TELEMETRY_DB", "/var/lib/factrail/telemetry.db"
        )
        self.retention_days = retention_days
        self._local = threading.local()
        self._lock = threading.Lock()
        self._ensure_db_dir()
        self._init_db()

    def _ensure_db_dir(self) -> None:
        """Create the parent directory of the telemetry DB if missing."""
        try:
            db_dir = os.path.dirname(self.db_path)
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir, mode=0o755, exist_ok=True)
        except Exception as exc:
            logger.warning("Telemetry DB dir creation error: %s", exc)

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn

    def _init_db(self) -> None:
        try:
            conn = self._get_conn()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    tool_name TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    error_type TEXT,
                    latency_ms REAL NOT NULL,
                    cache_status TEXT NOT NULL,
                    upstream_status TEXT,
                    client_family TEXT NOT NULL,
                    daily_client_hash TEXT NOT NULL,
                    utc_date TEXT NOT NULL
                )
                """
            )
            columns = {r[1] for r in conn.execute("PRAGMA table_info(telemetry)")}
            for name, decl in (("origin_class", "TEXT NOT NULL DEFAULT 'unknown'"),
                               ("server_version", "TEXT"), ("request_id", "TEXT"),
                               ("mcp_client_name", "TEXT"), ("mcp_client_version", "TEXT")):
                if name not in columns:
                    conn.execute(f"ALTER TABLE telemetry ADD COLUMN {name} {decl}")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_telemetry_timestamp ON telemetry(timestamp)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_telemetry_date ON telemetry(utc_date)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_telemetry_tool ON telemetry(tool_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_telemetry_client ON telemetry(daily_client_hash)"
            )
            conn.commit()
        except Exception as exc:
            logger.warning("Telemetry DB init error: %s", exc)

    def record(
        self,
        *,
        tool_name: str,
        success: bool,
        latency_ms: float,
        cache_status: str,
        upstream_status: Optional[str] = None,
        client_family: str = "unknown",
        daily_client_hash: str = "unknown",
        origin_class: str = "unknown",
        server_version: Optional[str] = None,
        request_id: Optional[str] = None,
        mcp_client_name: Optional[str] = None,
        mcp_client_version: Optional[str] = None,
    ) -> None:
        """Record a telemetry event. Never raises — fire-and-forget.

        Also probabilistically prunes old records (~1% chance per record)
        to enforce retention without a separate cron job.
        """
        try:
            now = _time.time()
            utc_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            conn = self._get_conn()
            conn.execute(
                """
                INSERT INTO telemetry
                    (timestamp, tool_name, success, error_type, latency_ms,
                     cache_status, upstream_status, client_family,
                     daily_client_hash, utc_date, origin_class, server_version, request_id, mcp_client_name, mcp_client_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    tool_name,
                    1 if success else 0,
                    None if success else "error",
                    latency_ms,
                    cache_status,
                    upstream_status,
                    client_family,
                    daily_client_hash,
                    utc_date,
                    origin_class if origin_class in ("external", "internal_test", "live_test", "manual", "inspector", "unknown") else "unknown",
                    server_version, request_id,
                    mcp_client_name, mcp_client_version,
                ),
            )
            conn.commit()
            # Probabilistic auto-prune: ~1% chance per record
            import random
            if random.random() < 0.01:
                self.purge_old()
        except Exception as exc:
            logger.debug("Telemetry record error: %s", exc)

    def purge_old(self) -> int:
        """Delete records older than retention_days. Returns count deleted."""
        try:
            cutoff = _time.time() - (self.retention_days * 86400)
            conn = self._get_conn()
            cursor = conn.execute(
                "DELETE FROM telemetry WHERE timestamp < ?", (cutoff,)
            )
            conn.commit()
            return cursor.rowcount or 0
        except Exception as exc:
            logger.warning("Telemetry purge error: %s", exc)
            return 0

    def get_stats(self, days: int) -> dict:
        """Get aggregated stats for the last N days."""
        try:
            cutoff = _time.time() - (days * 86400)
            conn = self._get_conn()

            # Total calls
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM telemetry WHERE timestamp >= ?",
                (cutoff,),
            ).fetchone()
            total_calls = row["cnt"] if row else 0

            # Unique clients
            row = conn.execute(
                "SELECT COUNT(DISTINCT daily_client_hash) as cnt FROM telemetry WHERE timestamp >= ?",
                (cutoff,),
            ).fetchone()
            unique_clients = row["cnt"] if row else 0

            # Success rate
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM telemetry WHERE timestamp >= ? AND success = 1",
                (cutoff,),
            ).fetchone()
            success_count = row["cnt"] if row else 0
            success_rate = (
                round(success_count / total_calls * 100, 1) if total_calls else 0.0
            )

            # Cache hit rate
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM telemetry WHERE timestamp >= ? AND cache_status = 'hit'",
                (cutoff,),
            ).fetchone()
            cache_hits = row["cnt"] if row else 0
            cache_hit_rate = (
                round(cache_hits / total_calls * 100, 1) if total_calls else 0.0
            )

            # Latency percentiles
            latencies = [
                r[0]
                for r in conn.execute(
                    "SELECT latency_ms FROM telemetry WHERE timestamp >= ? ORDER BY latency_ms",
                    (cutoff,),
                ).fetchall()
            ]
            p50 = self._percentile(latencies, 50) if latencies else 0.0
            p95 = self._percentile(latencies, 95) if latencies else 0.0

            # Calls by tool
            calls_by_tool = {}
            for r in conn.execute(
                "SELECT tool_name, COUNT(*) as cnt FROM telemetry WHERE timestamp >= ? GROUP BY tool_name ORDER BY cnt DESC",
                (cutoff,),
            ).fetchall():
                calls_by_tool[r["tool_name"]] = r["cnt"]

            # Calls by client family
            calls_by_client = {}
            for r in conn.execute(
                "SELECT client_family, COUNT(*) as cnt FROM telemetry WHERE timestamp >= ? GROUP BY client_family ORDER BY cnt DESC",
                (cutoff,),
            ).fetchall():
                calls_by_client[r["client_family"]] = r["cnt"]

            calls_by_origin = {}
            for r in conn.execute("SELECT origin_class, COUNT(*) AS cnt FROM telemetry WHERE timestamp >= ? GROUP BY origin_class ORDER BY cnt DESC", (cutoff,)).fetchall():
                calls_by_origin[r["origin_class"] or "unknown"] = r["cnt"]

            # Errors by type
            errors_by_type = {}
            for r in conn.execute(
                "SELECT error_type, COUNT(*) as cnt FROM telemetry WHERE timestamp >= ? AND success = 0 GROUP BY error_type ORDER BY cnt DESC",
                (cutoff,),
            ).fetchall():
                errors_by_type[r["error_type"] or "unknown"] = r["cnt"]

            return {
                "total_calls": total_calls,
                "unique_clients": unique_clients,
                "success_rate": success_rate,
                "cache_hit_rate": cache_hit_rate,
                "p50_latency_ms": round(p50, 2),
                "p95_latency_ms": round(p95, 2),
                "calls_by_tool": calls_by_tool,
                "calls_by_client_family": calls_by_client,
                "calls_by_origin": calls_by_origin,
                "errors_by_type": errors_by_type,
            }
        except Exception as exc:
            logger.warning("Telemetry stats error: %s", exc)
            return {
                "total_calls": 0,
                "unique_clients": 0,
                "success_rate": 0.0,
                "cache_hit_rate": 0.0,
                "p50_latency_ms": 0.0,
                "p95_latency_ms": 0.0,
                "calls_by_tool": {},
                "calls_by_client_family": {},
                "errors_by_type": {},
            }

    @staticmethod
    def _percentile(sorted_values: list[float], pct: float) -> float:
        """Compute percentile from a sorted list."""
        if not sorted_values:
            return 0.0
        idx = int(len(sorted_values) * pct / 100)
        idx = min(idx, len(sorted_values) - 1)
        return sorted_values[idx]

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None


_global_store: Optional[TelemetryStore] = None
_global_store_lock = threading.Lock()


def get_telemetry_store(
    db_path: Optional[str] = None,
    retention_days: int = 30,
) -> TelemetryStore:
    global _global_store
    if _global_store is None:
        with _global_store_lock:
            if _global_store is None:
                _global_store = TelemetryStore(
                    db_path=db_path,
                    retention_days=retention_days,
                )
    return _global_store


def is_telemetry_enabled() -> bool:
    return os.environ.get("FACTRAIL_TELEMETRY_ENABLED", "") == "1"


def record_tool_call(
    *,
    tool_name: str,
    success: bool,
    latency_ms: float,
    cache_status: str,
    upstream_status: Optional[str] = None,
    client_family: str = "unknown",
    daily_client_hash: str = "unknown",
    origin_class: str = "unknown",
    server_version: Optional[str] = None,
    request_id: Optional[str] = None,
    mcp_client_name: Optional[str] = None,
    mcp_client_version: Optional[str] = None,
) -> None:
    """Fire-and-forget telemetry record. Never raises."""
    if not is_telemetry_enabled():
        return
    try:
        store = get_telemetry_store()
        store.record(
            tool_name=tool_name,
            success=success,
            latency_ms=latency_ms,
            cache_status=cache_status,
            upstream_status=upstream_status,
            client_family=client_family,
            daily_client_hash=daily_client_hash,
            origin_class=origin_class, server_version=server_version, request_id=request_id,
            mcp_client_name=mcp_client_name, mcp_client_version=mcp_client_version,
        )
    except Exception:
        pass  # Telemetry must never break MCP


def build_client_context(request: Any) -> dict:
    """Build the anonymous client context from a request.

    Returns dict with client_family and daily_client_hash.
    Never includes raw IP or raw User-Agent.
    """
    try:
        client_name = request.headers.get("x-mcp-client-name", "").strip().lower()
        client_family = {"claude desktop": "claude", "claude code": "claude", "claude": "claude",
                         "chatgpt": "chatgpt", "cursor": "cursor", "visual studio code": "vscode",
                         "vscode": "vscode", "codex": "codex", "mcp inspector": "mcp_inspector"}.get(client_name, "unknown")
        if client_family == "mcp_inspector":
            origin_class = "inspector"
        elif request.headers.get("x-factrail-test-origin") in ("internal_test", "live_test", "manual"):
            origin_class = request.headers.get("x-factrail-test-origin")
        elif client_family != "unknown":
            origin_class = "external"
        else:
            origin_class = "unknown"
        utc_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        secret = _get_secret()
        # Use client name/version supplied by MCP initialize when available. Stateless deployments
        # may also send these normalized headers; never store raw header values.
        if origin_class == "unknown" and client_family != "unknown":
            origin_class = "external"
        # Daily hash is a one-way rotating digest; raw IP is not persisted.
        client_ip = _get_client_ip(request)
        daily_hash = _derive_daily_client_hash(client_ip, client_family, utc_date, secret)
        return {
            "client_family": client_family,
            "daily_client_hash": daily_hash,
            "origin_class": origin_class,
            "server_version": os.environ.get("FACTRAIL_VERSION", "2.4.0"),
            "request_id": getattr(getattr(request, "state", None), "request_id", None),
        }
    except Exception:
        return {
            "client_family": "unknown",
            "daily_client_hash": "unknown",
        }


if __name__ == "__main__":
    # Allow `python3 -m factrail.telemetry report` and `purge`
    from .telemetry_cli import main as cli_main
    cli_main()
