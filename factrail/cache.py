"""Persistent SQLite cache for Factrail with TTL, schema versioning, and stale-on-failure."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time as _time
from typing import Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


class SqliteCache:
    """Process-wide SQLite cache with configurable TTL and stale-on-failure."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        default_ttl: int = 3600,
        max_entries: int = 10000,
    ) -> None:
        self.db_path = db_path or os.environ.get("FACTRAIL_CACHE_PATH", "/tmp/factrail_cache.db")
        self.default_ttl = default_ttl
        self.max_entries = max_entries
        self._local = threading.local()
        self._stats = {"hits": 0, "misses": 0, "stales": 0, "evictions": 0, "errors": 0}
        self._stats_lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                expires_at REAL NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                schema_version INTEGER NOT NULL DEFAULT 1,
                source TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache(expires_at)")
        if ver < SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()

    def _make_key(self, identifier: str) -> str:
        return hashlib.sha256(identifier.encode()).hexdigest()

    def get(self, identifier: str, *, allow_stale: bool = False) -> Optional[dict]:
        try:
            key = self._make_key(identifier)
            conn = self._get_conn()
            row = conn.execute(
                "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                self._increment_stat("misses")
                return None

            now = _time.time()
            if row["expires_at"] > now:
                self._increment_stat("hits")
                return json.loads(row["value"])

            if allow_stale:
                self._increment_stat("stales")
                return json.loads(row["value"])

            self._increment_stat("misses")
            return None
        except Exception as exc:
            logger.warning("Cache read error: %s", exc)
            self._increment_stat("errors")
            return None

    def set(
        self,
        identifier: str,
        data: dict,
        ttl: Optional[int] = None,
        source: Optional[str] = None,
    ) -> None:
        try:
            key = self._make_key(identifier)
            now = _time.time()
            ttl = ttl or self.default_ttl
            conn = self._get_conn()
            conn.execute(
                """
                INSERT INTO cache (key, value, expires_at, created_at, updated_at, schema_version, source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at,
                    schema_version = excluded.schema_version,
                    source = excluded.source
                """,
                (key, json.dumps(data), now + ttl, now, now, SCHEMA_VERSION, source),
            )
            conn.commit()
            self._maybe_evict(conn)
        except Exception as exc:
            logger.warning("Cache write error: %s", exc)
            self._increment_stat("errors")

    def _maybe_evict(self, conn: sqlite3.Connection) -> None:
        count = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
        if count > self.max_entries:
            evict_count = count - self.max_entries + 100
            conn.execute(
                "DELETE FROM cache WHERE key IN (SELECT key FROM cache ORDER BY updated_at ASC LIMIT ?)",
                (evict_count,),
            )
            conn.commit()
            self._increment_stat("evictions", evict_count)

    def invalidate(self, identifier: str) -> None:
        try:
            key = self._make_key(identifier)
            conn = self._get_conn()
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            conn.commit()
        except Exception as exc:
            logger.warning("Cache invalidate error: %s", exc)

    def clear(self) -> None:
        conn = self._get_conn()
        conn.execute("DELETE FROM cache")
        conn.commit()

    def stats(self) -> dict:
        with self._stats_lock:
            total = self._stats["hits"] + self._stats["misses"]
            return {
                **self._stats,
                "hit_rate_pct": round(self._stats["hits"] / total * 100, 1) if total else 0.0,
                "db_path": self.db_path,
            }

    def _increment_stat(self, name: str, amount: int = 1) -> None:
        with self._stats_lock:
            self._stats[name] += amount

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None


_global_cache: Optional[SqliteCache] = None
_global_cache_lock = threading.Lock()


def get_cache(
    db_path: Optional[str] = None,
    default_ttl: int = 3600,
    max_entries: int = 10000,
) -> SqliteCache:
    global _global_cache
    if _global_cache is None:
        with _global_cache_lock:
            if _global_cache is None:
                _global_cache = SqliteCache(
                    db_path=db_path,
                    default_ttl=default_ttl,
                    max_entries=max_entries,
                )
    return _global_cache
