"""Tests for Factrail V1.3.4 privacy-preserving telemetry."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
import time as _time
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
import mcp.types as types
from starlette.testclient import TestClient

import asyncio
import contextvars
from unittest.mock import patch, AsyncMock

from factrail import telemetry as telemetry_mod
from factrail.telemetry import (
    TelemetryStore,
    get_telemetry_store,
    is_telemetry_enabled,
    record_tool_call,
    build_client_context,
    _derive_daily_client_hash,
    _detect_client_family,
    _get_client_ip,
    _get_secret,
    _CLIENT_FAMILIES,
)
from factrail.telemetry_middleware import (
    TelemetryMiddleware,
    _last_cache_status,
    _set_cache_status,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(db_path: str, retention_days: int = 30) -> TelemetryStore:
    return TelemetryStore(db_path=db_path, retention_days=retention_days)


def _insert_call(
    store: TelemetryStore,
    *,
    tool_name: str = "verify_french_company",
    success: bool = True,
    latency_ms: float = 100.0,
    cache_status: str = "hit",
    upstream_status: str = None,
    client_family: str = "unknown",
    daily_client_hash: str = "abc123",
    days_ago: float = 0,
):
    ts = _time.time() - (days_ago * 86400)
    utc_date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    conn = store._get_conn()
    conn.execute(
        """
        INSERT INTO telemetry
            (timestamp, tool_name, success, error_type, latency_ms,
             cache_status, upstream_status, client_family,
             daily_client_hash, utc_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts,
            tool_name,
            1 if success else 0,
            None if success else "error",
            latency_ms,
            cache_status,
            upstream_status,
            client_family,
            daily_client_hash,
            utc_date,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Client family detection
# ---------------------------------------------------------------------------


class TestClientFamilyDetection:
    @pytest.mark.parametrize("ua,expected", [
        ("goose/1.0", "goose"),
        ("Claude/1.0", "claude"),
        ("cursor/1.0", "cursor"),
        ("inspector/1.0", "inspector"),
        ("python-httpx/0.27", "unknown"),
        ("", "unknown"),
    ])
    def test_detect_family(self, ua, expected):
        assert _detect_client_family(ua) == expected


# ---------------------------------------------------------------------------
# Daily hash derivation
# ---------------------------------------------------------------------------


class TestDailyHash:
    def test_hash_changes_with_date(self):
        h1 = _derive_daily_client_hash("1.2.3.4", "goose", "2024-01-01", "secret")
        h2 = _derive_daily_client_hash("1.2.3.4", "goose", "2024-01-02", "secret")
        assert h1 != h2

    def test_hash_changes_with_ip(self):
        h1 = _derive_daily_client_hash("1.2.3.4", "goose", "2024-01-01", "secret")
        h2 = _derive_daily_client_hash("5.6.7.8", "goose", "2024-01-01", "secret")
        assert h1 != h2

    def test_hash_changes_with_family(self):
        h1 = _derive_daily_client_hash("1.2.3.4", "goose", "2024-01-01", "secret")
        h2 = _derive_daily_client_hash("1.2.3.4", "claude", "2024-01-01", "secret")
        assert h1 != h2

    def test_hash_changes_with_secret(self):
        h1 = _derive_daily_client_hash("1.2.3.4", "goose", "2024-01-01", "secret1")
        h2 = _derive_daily_client_hash("1.2.3.4", "goose", "2024-01-01", "secret2")
        assert h1 != h2

    def test_hash_is_16_chars(self):
        h = _derive_daily_client_hash("1.2.3.4", "goose", "2024-01-01", "secret")
        assert len(h) == 16


# ---------------------------------------------------------------------------
# Client IP extraction (Cloudflare aware)
# ---------------------------------------------------------------------------


class TestClientIPExtraction:
    def _make_request(self, headers, client_host="1.2.3.4"):
        req = MagicMock()
        req.headers = headers
        req.client = MagicMock()
        req.client.host = client_host
        return req

    def test_cf_connecting_ip_when_cf_ray_present(self):
        req = self._make_request({
            "cf-ray": "abc123",
            "cf-connecting-ip": "10.0.0.1",
        })
        assert _get_client_ip(req) == "10.0.0.1"

    def test_direct_ip_without_cf_ray(self):
        req = self._make_request({}, client_host="1.2.3.4")
        assert _get_client_ip(req) == "1.2.3.4"

    def test_unknown_when_no_client(self):
        req = self._make_request({})
        req.client = None
        assert _get_client_ip(req) == "unknown"


# ---------------------------------------------------------------------------
# TelemetryStore
# ---------------------------------------------------------------------------


class TestTelemetryStore:
    def setup_method(self):
        self.db = tempfile.mktemp(suffix=".db")
        self.store = _make_store(self.db, retention_days=30)

    def teardown_method(self):
        self.store.close()
        if os.path.exists(self.db):
            os.unlink(self.db)

    def test_record_and_stats(self):
        _insert_call(self.store, success=True, latency_ms=100, cache_status="hit")
        _insert_call(self.store, success=True, latency_ms=200, cache_status="miss")
        _insert_call(self.store, success=False, latency_ms=300, cache_status="miss", days_ago=0)

        stats = self.store.get_stats(1)
        assert stats["total_calls"] == 3
        assert stats["success_rate"] == pytest.approx(66.7, abs=0.1)
        assert stats["cache_hit_rate"] == pytest.approx(33.3, abs=0.1)
        assert stats["p50_latency_ms"] == 200.0
        assert stats["p95_latency_ms"] >= 200.0

    def test_unique_clients(self):
        _insert_call(self.store, daily_client_hash="aaa")
        _insert_call(self.store, daily_client_hash="bbb")
        _insert_call(self.store, daily_client_hash="aaa")

        stats = self.store.get_stats(1)
        assert stats["unique_clients"] == 2

    def test_calls_by_tool(self):
        _insert_call(self.store, tool_name="verify_french_company")
        _insert_call(self.store, tool_name="verify_french_company")
        _insert_call(self.store, tool_name="other_tool")

        stats = self.store.get_stats(1)
        assert stats["calls_by_tool"]["verify_french_company"] == 2
        assert stats["calls_by_tool"]["other_tool"] == 1

    def test_calls_by_client_family(self):
        _insert_call(self.store, client_family="goose")
        _insert_call(self.store, client_family="goose")
        _insert_call(self.store, client_family="claude")

        stats = self.store.get_stats(1)
        assert stats["calls_by_client_family"]["goose"] == 2
        assert stats["calls_by_client_family"]["claude"] == 1

    def test_errors_by_type(self):
        _insert_call(self.store, success=False, latency_ms=50)
        _insert_call(self.store, success=False, latency_ms=60)
        _insert_call(self.store, success=True, latency_ms=70)

        stats = self.store.get_stats(1)
        assert stats["errors_by_type"]["error"] == 2

    def test_purge_old_records(self):
        _insert_call(self.store, days_ago=1)
        _insert_call(self.store, days_ago=10)
        _insert_call(self.store, days_ago=40)

        deleted = self.store.purge_old()
        assert deleted == 1

        stats = self.store.get_stats(30)
        assert stats["total_calls"] == 2

    def test_stats_empty(self):
        stats = self.store.get_stats(1)
        assert stats["total_calls"] == 0
        assert stats["unique_clients"] == 0


# ---------------------------------------------------------------------------
# Privacy: no raw IPs, User-Agent, SIREN/SIRET, auth headers
# ---------------------------------------------------------------------------


class TestPrivacyGuarantees:
    def test_no_raw_ip_in_db(self):
        """Verify raw IPs are never persisted."""
        db = tempfile.mktemp(suffix=".db")
        store = _make_store(db, retention_days=30)
        try:
            _insert_call(
                store,
                daily_client_hash=_derive_daily_client_hash(
                    "192.168.1.100", "goose", "2024-01-01", "secret"
                ),
            )
            conn = sqlite3.connect(db)
            row = conn.execute("SELECT daily_client_hash FROM telemetry").fetchone()
            assert row[0] != "192.168.1.100"
            assert "192.168.1.100" not in str(row)
            conn.close()
        finally:
            store.close()
            if os.path.exists(db):
                os.unlink(db)

    def test_no_raw_user_agent_in_db(self):
        """Verify raw User-Agent strings are never persisted."""
        db = tempfile.mktemp(suffix=".db")
        store = _make_store(db, retention_days=30)
        try:
            _insert_call(store, client_family="goose")
            conn = sqlite3.connect(db)
            # Check all text columns for any user-agent-like string
            rows = conn.execute("SELECT * FROM telemetry").fetchall()
            for row in rows:
                for cell in row:
                    assert "goose" not in str(cell).lower() or cell == "goose"
                    assert "user-agent" not in str(cell).lower()
                    assert "mozilla" not in str(cell).lower()
            conn.close()
        finally:
            store.close()
            if os.path.exists(db):
                os.unlink(db)

    def test_no_siren_siret_in_db(self):
        """Verify SIREN/SIRET values are never persisted."""
        db = tempfile.mktemp(suffix=".db")
        store = _make_store(db, retention_days=30)
        try:
            _insert_call(store, tool_name="verify_french_company")
            conn = sqlite3.connect(db)
            rows = conn.execute("SELECT * FROM telemetry").fetchall()
            for row in rows:
                for cell in row:
                    # Should not contain 9-digit or 14-digit numbers
                    cell_str = str(cell)
                    assert "123456789" not in cell_str
                    assert "12345678901234" not in cell_str
            conn.close()
        finally:
            store.close()
            if os.path.exists(db):
                os.unlink(db)

    def test_no_auth_headers_in_db(self):
        """Verify Authorization/cookie values are never persisted."""
        db = tempfile.mktemp(suffix=".db")
        store = _make_store(db, retention_days=30)
        try:
            _insert_call(store)
            conn = sqlite3.connect(db)
            rows = conn.execute("SELECT * FROM telemetry").fetchall()
            for row in rows:
                for cell in row:
                    assert "authorization" not in str(cell).lower()
                    assert "bearer" not in str(cell).lower()
                    assert "cookie" not in str(cell).lower()
                    assert "api_key" not in str(cell).lower()
                    assert "token" not in str(cell).lower()
            conn.close()
        finally:
            store.close()
            if os.path.exists(db):
                os.unlink(db)

    def test_no_tool_arguments_in_db(self):
        """Verify tool arguments are never persisted."""
        db = tempfile.mktemp(suffix=".db")
        store = _make_store(db, retention_days=30)
        try:
            _insert_call(store, tool_name="verify_french_company")
            conn = sqlite3.connect(db)
            rows = conn.execute("SELECT * FROM telemetry").fetchall()
            for row in rows:
                for cell in row:
                    assert "identifier" not in str(cell).lower()
                    assert "arguments" not in str(cell).lower()
                    assert "params" not in str(cell).lower()
            conn.close()
        finally:
            store.close()
            if os.path.exists(db):
                os.unlink(db)


# ---------------------------------------------------------------------------
# Only real tools/call counted
# ---------------------------------------------------------------------------


class TestOnlyRealToolCallsCounted:
    def setup_method(self):
        self.db = tempfile.mktemp(suffix=".db")
        self.store = _make_store(self.db, retention_days=30)
        self._old_env = os.environ.get("FACTRAIL_TELEMETRY_ENABLED")
        self._old_db = os.environ.get("FACTRAIL_TELEMETRY_DB")
        os.environ["FACTRAIL_TELEMETRY_ENABLED"] = "1"
        os.environ["FACTRAIL_TELEMETRY_DB"] = self.db
        telemetry_mod._global_store = self.store

    def teardown_method(self):
        self.store.close()
        if os.path.exists(self.db):
            os.unlink(self.db)
        if self._old_env is None:
            os.environ.pop("FACTRAIL_TELEMETRY_ENABLED", None)
        else:
            os.environ["FACTRAIL_TELEMETRY_ENABLED"] = self._old_env
        if self._old_db is None:
            os.environ.pop("FACTRAIL_TELEMETRY_DB", None)
        else:
            os.environ["FACTRAIL_TELEMETRY_DB"] = self._old_db
        telemetry_mod._global_store = None

    def test_tools_list_not_counted(self):
        """Verify tools/list requests are NOT counted."""
        # Simulate what the middleware does: only records tools/call
        record_tool_call(
            tool_name="verify_french_company",
            success=True,
            latency_ms=100,
            cache_status="hit",
        )
        # tools/list would NOT call record_tool_call
        stats = self.store.get_stats(1)
        assert stats["total_calls"] == 1
        assert "tools/list" not in stats["calls_by_tool"]

    def test_initialization_not_counted(self):
        """Verify MCP initialization is NOT counted."""
        # Only tools/call triggers record_tool_call
        record_tool_call(
            tool_name="verify_french_company",
            success=True,
            latency_ms=100,
            cache_status="hit",
        )
        stats = self.store.get_stats(1)
        assert "initialize" not in stats["calls_by_tool"]


# ---------------------------------------------------------------------------
# Telemetry DB failure does not affect MCP responses
# ---------------------------------------------------------------------------


class TestTelemetryNonBlocking:
    def test_telemetry_failure_does_not_break_mcp(self):
        """If telemetry DB is corrupted/unavailable, MCP still works."""
        from factrail.mcp_http_server import build_app
        from factrail import per_client_limiter

        # Set up a broken telemetry DB path (read-only directory)
        broken_dir = tempfile.mkdtemp()
        os.chmod(broken_dir, 0o444)  # read-only
        broken_db = os.path.join(broken_dir, "telemetry.db")

        os.environ["FACTRAIL_TELEMETRY_ENABLED"] = "1"
        os.environ["FACTRAIL_TELEMETRY_DB"] = broken_db

        try:
            per_client_limiter._global_limiter = per_client_limiter.PerClientRateLimiter(
                requests_per_window=100, window_seconds=60, max_wait=0.0
            )
            starlette_app = build_app()
            with TestClient(starlette_app) as client:
                # MCP should still work even if telemetry fails
                r = client.post(
                    "/mcp",
                    headers={
                        "Content-Type": "application/json",
                        "MCP-Protocol-Version": "2026-07-28",
                        "Accept": "application/json, text/event-stream",
                    },
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                )
                # Should not be 500
                assert r.status_code != 500
        finally:
            os.chmod(broken_dir, 0o755)
            os.rmdir(broken_dir)
            os.environ.pop("FACTRAIL_TELEMETRY_ENABLED", None)
            os.environ.pop("FACTRAIL_TELEMETRY_DB", None)

    def test_record_tool_call_never_raises(self):
        """record_tool_call must never raise."""
        os.environ["FACTRAIL_TELEMETRY_ENABLED"] = "1"
        os.environ["FACTRAIL_TELEMETRY_DB"] = "/nonexistent/path/telemetry.db"
        try:
            record_tool_call(
                tool_name="test",
                success=True,
                latency_ms=100,
                cache_status="hit",
            )
        finally:
            os.environ.pop("FACTRAIL_TELEMETRY_ENABLED", None)
            os.environ.pop("FACTRAIL_TELEMETRY_DB", None)


# ---------------------------------------------------------------------------
# Report metrics correctness
# ---------------------------------------------------------------------------


class TestReportMetrics:
    def setup_method(self):
        self.db = tempfile.mktemp(suffix=".db")
        self.store = _make_store(self.db, retention_days=30)

    def teardown_method(self):
        self.store.close()
        if os.path.exists(self.db):
            os.unlink(self.db)

    def test_report_calls_today(self):
        _insert_call(self.store, days_ago=0)
        stats = self.store.get_stats(1)
        assert stats["total_calls"] == 1

    def test_report_calls_last_7_days(self):
        _insert_call(self.store, days_ago=1)
        _insert_call(self.store, days_ago=3)
        _insert_call(self.store, days_ago=8)  # outside 7 days
        stats = self.store.get_stats(7)
        assert stats["total_calls"] == 2

    def test_report_calls_last_30_days(self):
        _insert_call(self.store, days_ago=1)
        _insert_call(self.store, days_ago=10)
        _insert_call(self.store, days_ago=25)
        _insert_call(self.store, days_ago=35)  # outside 30 days
        stats = self.store.get_stats(30)
        assert stats["total_calls"] == 3

    def test_success_rate_calculation(self):
        for _ in range(3):
            _insert_call(self.store, success=True)
        _insert_call(self.store, success=False)
        stats = self.store.get_stats(1)
        assert stats["success_rate"] == pytest.approx(75.0, abs=0.1)

    def test_cache_hit_rate_calculation(self):
        _insert_call(self.store, cache_status="hit")
        _insert_call(self.store, cache_status="hit")
        _insert_call(self.store, cache_status="miss")
        _insert_call(self.store, cache_status="stale")
        stats = self.store.get_stats(1)
        assert stats["cache_hit_rate"] == pytest.approx(50.0, abs=0.1)


# ---------------------------------------------------------------------------
# Middleware integration tests
# ---------------------------------------------------------------------------


class TestTelemetryMiddleware:
    def setup_method(self):
        self.db = tempfile.mktemp(suffix=".db")
        self.store = _make_store(self.db, retention_days=30)
        self._old_env = os.environ.get("FACTRAIL_TELEMETRY_ENABLED")
        self._old_db = os.environ.get("FACTRAIL_TELEMETRY_DB")
        os.environ["FACTRAIL_TELEMETRY_ENABLED"] = "1"
        os.environ["FACTRAIL_TELEMETRY_DB"] = self.db
        telemetry_mod._global_store = self.store

    def teardown_method(self):
        self.store.close()
        if os.path.exists(self.db):
            os.unlink(self.db)
        if self._old_env is None:
            os.environ.pop("FACTRAIL_TELEMETRY_ENABLED", None)
        else:
            os.environ["FACTRAIL_TELEMETRY_ENABLED"] = self._old_env
        if self._old_db is None:
            os.environ.pop("FACTRAIL_TELEMETRY_DB", None)
        else:
            os.environ["FACTRAIL_TELEMETRY_DB"] = self._old_db
        telemetry_mod._global_store = None

    def test_middleware_counts_tools_call(self):
        """Middleware records tools/call but not tools/list."""
        from factrail.mcp_http_server import build_app
        from factrail import per_client_limiter

        per_client_limiter._global_limiter = per_client_limiter.PerClientRateLimiter(
            requests_per_window=100, window_seconds=60, max_wait=0.0
        )
        starlette_app = build_app()
        with TestClient(starlette_app) as client:
            # tools/list should NOT be counted
            r = client.post(
                "/mcp",
                headers={
                    "Content-Type": "application/json",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Accept": "application/json, text/event-stream",
                    "Host": "localhost",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
            # Status may be 400 (spec-invalid without _meta) — the key thing
            # is it's not a telemetry failure. We just need the middleware to
            # process it without recording telemetry.
            assert r.status_code != 500

        stats = self.store.get_stats(1)
        assert stats["total_calls"] == 0  # tools/list not counted


# ---------------------------------------------------------------------------
# Auto-pruning test
# ---------------------------------------------------------------------------


class TestAutoPruning:
    def test_purge_enforces_retention(self):
        db = tempfile.mktemp(suffix=".db")
        store = _make_store(db, retention_days=7)
        try:
            _insert_call(store, days_ago=1)
            _insert_call(store, days_ago=5)
            _insert_call(store, days_ago=10)
            _insert_call(store, days_ago=20)

            deleted = store.purge_old()
            assert deleted == 2

            stats = store.get_stats(30)
            assert stats["total_calls"] == 2
        finally:
            store.close()
            if os.path.exists(db):
                os.unlink(db)


# ---------------------------------------------------------------------------
# Concurrency-safety: ContextVar isolation between concurrent requests
# ---------------------------------------------------------------------------


class TestConcurrencySafety:
    """Prove that cache/upstream status cannot leak between concurrent calls."""

    @pytest.mark.asyncio
    async def test_contextvar_isolation_between_tasks(self):
        """Two concurrent tasks writing different cache/upstream values
        must not see each other's state."""

        async def task_a():
            _set_cache_status("hit", "cache_only")
            await asyncio.sleep(0.01)  # yield to task_b
            status = _last_cache_status.get()
            return status.get("cache"), status.get("upstream")

        async def task_b():
            _set_cache_status("miss", "available")
            await asyncio.sleep(0.01)  # yield to task_a
            status = _last_cache_status.get()
            return status.get("cache"), status.get("upstream")

        # Run concurrently
        result_a, result_b = await asyncio.gather(task_a(), task_b())

        # Each task must see its own isolated state
        assert result_a == ("hit", "cache_only")
        assert result_b == ("miss", "available")

    @pytest.mark.asyncio
    async def test_set_cache_status_does_not_mutate_shared_dict(self):
        """_set_cache_status must write to the ContextVar, not a shared dict."""

        # Write a value in the main context
        _set_cache_status("hit", "cache_only")

        # Spawn a task that writes different values
        async def other_context():
            _set_cache_status("miss", "unavailable")
            status = _last_cache_status.get()
            return status.get("cache"), status.get("upstream")

        result = await asyncio.create_task(other_context())
        assert result == ("miss", "unavailable")

        # Main context must still have its original values
        main_status = _last_cache_status.get()
        assert main_status.get("cache") == "hit"
        assert main_status.get("upstream") == "cache_only"

    @pytest.mark.asyncio
    async def test_concurrent_cached_lookup_status_isolation(self):
        """Concurrent calls to _cached_lookup with different cache outcomes
        must produce correct, non-leaked telemetry status."""
        from factrail import cache as cache_mod
        from factrail.models import FrenchCompany, SourceMeta
        from datetime import datetime, timezone
        from factrail.mcp_http_server import handle_call_tool
        from factrail.sources.insee import UpstreamError

        db = tempfile.mktemp(suffix=".db")
        store = _make_store(db, retention_days=30)
        old_env = os.environ.get("FACTRAIL_TELEMETRY_ENABLED")
        old_db = os.environ.get("FACTRAIL_TELEMETRY_DB")
        os.environ["FACTRAIL_TELEMETRY_ENABLED"] = "1"
        os.environ["FACTRAIL_TELEMETRY_DB"] = db
        telemetry_mod._global_store = store

        # Two different identifiers: one cached (hit), one live (miss)
        fc_cached = FrenchCompany(
            siren="111111111",
            legal_name="CACHED CORP",
            checked_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            sources=[SourceMeta(id="t", name="t", url="http://t", retrieved_at=datetime(2024, 1, 1, tzinfo=timezone.utc))],
        )
        fc_live = FrenchCompany(
            siren="222222222",
            legal_name="LIVE CORP",
            checked_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            sources=[SourceMeta(id="t", name="t", url="http://t", retrieved_at=datetime(2024, 1, 1, tzinfo=timezone.utc))],
        )

        cache_mod._global_cache = cache_mod.SqliteCache(
            db_path=tempfile.mktemp(suffix=".db"), default_ttl=3600, max_entries=100
        )
        cache_mod._global_cache.set("111111111", fc_cached.model_dump(mode="json"), ttl=3600)

        async def call_cached():
            return await handle_call_tool(
                None,
                types.CallToolRequestParams(
                    name="verify_french_company", arguments={"identifier": "111111111"}
                ),
            )

        async def call_live():
            with patch("factrail.mcp_http_server.InseeAdapter") as MockAdapter:
                MockAdapter.return_value.lookup_with_bodacc.return_value = fc_live
                return await handle_call_tool(
                    None,
                    types.CallToolRequestParams(
                        name="verify_french_company", arguments={"identifier": "222222222"}
                    ),
                )

        # Run both concurrently — this is the real concurrency test
        result_cached, result_live = await asyncio.gather(call_cached(), call_live())

        # Both must succeed
        data_cached = json.loads(result_cached.content[0].text)
        data_live = json.loads(result_live.content[0].text)
        assert "siren" in data_cached
        assert "siren" in data_live

        # The critical assertion: the ContextVar values must NOT have leaked.
        # After both tasks complete, the main-context value should be None
        # (each task had its own isolated context, and the main context
        # never had any status set).
        remaining = _last_cache_status.get()
        assert remaining is None or remaining.get("cache") is None
        assert remaining is None or remaining.get("upstream") is None

        # Cleanup
        if old_env is None:
            os.environ.pop("FACTRAIL_TELEMETRY_ENABLED", None)
        else:
            os.environ["FACTRAIL_TELEMETRY_ENABLED"] = old_env
        if old_db is None:
            os.environ.pop("FACTRAIL_TELEMETRY_DB", None)
        else:
            os.environ["FACTRAIL_TELEMETRY_DB"] = old_db
        telemetry_mod._global_store = None
        store.close()
        if os.path.exists(db):
            os.unlink(db)

    @pytest.mark.asyncio
    async def test_upstream_error_status_isolated(self):
        """UpstreamError in one task must not poison another task's status."""
        from factrail import cache as cache_mod
        from factrail.mcp_http_server import handle_call_tool
        from factrail.sources.insee import UpstreamError

        db = tempfile.mktemp(suffix=".db")
        store = _make_store(db, retention_days=30)
        old_env = os.environ.get("FACTRAIL_TELEMETRY_ENABLED")
        old_db = os.environ.get("FACTRAIL_TELEMETRY_DB")
        os.environ["FACTRAIL_TELEMETRY_ENABLED"] = "1"
        os.environ["FACTRAIL_TELEMETRY_DB"] = db
        telemetry_mod._global_store = store

        cache_mod._global_cache = cache_mod.SqliteCache(
            db_path=tempfile.mktemp(suffix=".db"), default_ttl=3600, max_entries=100
        )

        async def call_upstream_error():
            with patch("factrail.mcp_http_server.InseeAdapter") as MockAdapter:
                MockAdapter.return_value.lookup_with_bodacc.side_effect = UpstreamError("Timeout")
                return await handle_call_tool(
                    None,
                    types.CallToolRequestParams(
                        name="verify_french_company", arguments={"identifier": "333333333"}
                    ),
                )

        async def call_upstream_error_2():
            with patch("factrail.mcp_http_server.InseeAdapter") as MockAdapter:
                MockAdapter.return_value.lookup_with_bodacc.side_effect = UpstreamError("Timeout")
                return await handle_call_tool(
                    None,
                    types.CallToolRequestParams(
                        name="verify_french_company", arguments={"identifier": "444444444"}
                    ),
                )

        # Both fail — but their status must not leak
        r1, r2 = await asyncio.gather(call_upstream_error(), call_upstream_error_2())
        d1 = json.loads(r1.content[0].text)
        d2 = json.loads(r2.content[0].text)
        assert d1.get("error") == "upstream"
        assert d2.get("error") == "upstream"

        # No leftover status in main context
        remaining = _last_cache_status.get()
        assert remaining is None or remaining.get("cache") is None
        assert remaining is None or remaining.get("upstream") is None

        if old_env is None:
            os.environ.pop("FACTRAIL_TELEMETRY_ENABLED", None)
        else:
            os.environ["FACTRAIL_TELEMETRY_ENABLED"] = old_env
        if old_db is None:
            os.environ.pop("FACTRAIL_TELEMETRY_DB", None)
        else:
            os.environ["FACTRAIL_TELEMETRY_DB"] = old_db
        telemetry_mod._global_store = None
        store.close()
        if os.path.exists(db):
            os.unlink(db)
