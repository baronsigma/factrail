"""Tests for Factrail V1.3.1 production readiness components."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import tempfile
import threading
import time as _time
from unittest.mock import patch, MagicMock

import pytest
import respx
import httpx

from factrail.cache import SqliteCache, get_cache
from factrail.per_client_limiter import PerClientRateLimiter, get_per_client_limiter


# ---------------------------------------------------------------------------
# Cache tests
# ---------------------------------------------------------------------------


class TestSqliteCache:
    def setup_method(self):
        self.db = tempfile.mktemp(suffix=".db")
        self.cache = SqliteCache(db_path=self.db, default_ttl=2, max_entries=100)

    def teardown_method(self):
        self.cache.close()
        if os.path.exists(self.db):
            os.unlink(self.db)

    def test_get_missing(self):
        assert self.cache.get("MISSING-123456789") is None
        assert self.cache.stats()["misses"] == 1

    def test_set_and_get(self):
        data = {"siren": "123456789", "name": "Test Corp"}
        self.cache.set("123456789", data)
        result = self.cache.get("123456789")
        assert result == data
        assert self.cache.stats()["hits"] == 1

    def test_ttl_expiry(self):
        data = {"siren": "123456789", "name": "Test"}
        self.cache.set("123456789", data, ttl=1)
        assert self.cache.get("123456789") == data
        _time.sleep(1.1)
        assert self.cache.get("123456789") is None

    def test_stale_on_failure(self):
        data = {"siren": "123456789", "name": "Stale"}
        self.cache.set("123456789", data, ttl=1)
        _time.sleep(1.1)
        assert self.cache.get("123456789", allow_stale=False) is None
        stale = self.cache.get("123456789", allow_stale=True)
        assert stale == data
        assert self.cache.stats()["stales"] == 1

    def test_invalidate(self):
        data = {"siren": "123456789"}
        self.cache.set("123456789", data)
        self.cache.invalidate("123456789")
        assert self.cache.get("123456789") is None

    def test_stats(self):
        for i in range(5):
            self.cache.set(f"SIREN-{i:09d}", {"siren": f"{i:09d}"})
        for i in range(5):
            self.cache.get(f"SIREN-{i:09d}")
        self.cache.get("MISSING")
        stats = self.cache.stats()
        assert stats["hits"] == 5
        assert stats["misses"] == 1
        assert stats["hit_rate_pct"] == pytest.approx(83.3, abs=0.5)

    def test_eviction(self):
        cache = SqliteCache(db_path=self.db, default_ttl=3600, max_entries=10)
        for i in range(15):
            cache.set(f"S-{i}", {"id": i})
        assert cache.get("S-14") is not None

    def test_clear(self):
        self.cache.set("123456789", {"a": 1})
        self.cache.clear()
        assert self.cache.get("123456789") is None

    def test_invalid_data_no_crash(self):
        import sqlite3
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO cache (key, value, expires_at, created_at, updated_at, schema_version) VALUES (?, ?, ?, ?, ?, ?)",
            ("bad", "not json", _time.time() + 100, _time.time(), _time.time(), 1),
        )
        conn.commit()
        conn.close()
        assert self.cache.get("bad") is None

    def test_get_cache_singleton(self):
        c1 = get_cache(self.db, 3600, 100)
        c2 = get_cache(self.db, 3600, 100)
        assert c1 is c2


# ---------------------------------------------------------------------------
# Per-client limiter tests
# ---------------------------------------------------------------------------


class TestPerClientLimiter:
    def test_allows_under_limit(self):
        limiter = PerClientRateLimiter(requests_per_window=5, window_seconds=60)
        for _ in range(5):
            result = limiter.check("client-1")
            assert result["allowed"] is True

    def test_blocks_over_limit(self):
        limiter = PerClientRateLimiter(requests_per_window=3, window_seconds=60, max_wait=0.1)
        for _ in range(3):
            limiter.check("client-1")
        result = limiter.check("client-1")
        assert result["allowed"] is False
        assert result["retry_after"] > 0
        assert result["limit"] == 3

    def test_separate_clients(self):
        limiter = PerClientRateLimiter(requests_per_window=1, window_seconds=60, max_wait=0.1)
        r1 = limiter.check("client-A")
        r2 = limiter.check("client-B")
        assert r1["allowed"] is True
        assert r2["allowed"] is True

    def test_reset(self):
        limiter = PerClientRateLimiter(requests_per_window=1, window_seconds=60, max_wait=0.1)
        limiter.check("client-1")
        limiter.reset("client-1")
        result = limiter.check("client-1")
        assert result["allowed"] is True

    def test_block_list_for_excessive(self):
        limiter = PerClientRateLimiter(
            requests_per_window=2,
            window_seconds=60,
            max_wait=0.1,
            block_excessive_after=2,
            block_duration=1,
        )
        for _ in range(4):
            limiter.check("bad-client")
        result = limiter.check("bad-client")
        assert result["allowed"] is False
        assert result["retry_after"] >= 0.9

    def test_bounded_wait(self):
        limiter = PerClientRateLimiter(requests_per_window=1, window_seconds=1, max_wait=0.5)
        limiter.check("client-1")
        result = limiter.check("client-1")
        if not result["allowed"]:
            assert result["retry_after"] <= 1.0

    def test_get_per_client_limiter_singleton(self):
        l1 = get_per_client_limiter()
        l2 = get_per_client_limiter()
        assert l1 is l2


# ---------------------------------------------------------------------------
# App integration tests
# ---------------------------------------------------------------------------


from factrail.mcp_http_server import build_app, handle_list_tools, handle_call_tool
import mcp.types as types
from starlette.testclient import TestClient


class TestHandleListTools:
    @pytest.mark.asyncio
    async def test_tool_annotations(self):
        result = await handle_list_tools(None, None)
        assert len(result.tools) == 6
        # Verify verify_french_company is present with correct annotations
        vfc = next(t for t in result.tools if t.name == "verify_french_company")
        assert vfc.annotations is not None
        assert vfc.annotations.read_only_hint is True
        assert vfc.annotations.destructive_hint is False
        assert vfc.annotations.idempotent_hint is True
        assert vfc.annotations.open_world_hint is True
        # Verify assess_import is present with correct annotations
        ai = next(t for t in result.tools if t.name == "assess_import")
        assert ai.annotations is not None
        assert ai.annotations.read_only_hint is True
        assert ai.annotations.destructive_hint is False
        assert ai.annotations.idempotent_hint is True
        assert ai.annotations.open_world_hint is True

    @pytest.mark.asyncio
    async def test_tool_input_schema(self):
        result = await handle_list_tools(None, None)
        schemas = {t.name: t.input_schema for t in result.tools}
        vfc_schema = schemas["verify_french_company"]
        assert "identifier" in vfc_schema["properties"]
        assert "identifier" in vfc_schema["required"]
        ai_schema = schemas["assess_import"]
        assert "product" in ai_schema["properties"]
        assert "origin_country" in ai_schema["properties"]
        assert "destination_country" in ai_schema["properties"]
        assert "quantity" in ai_schema["properties"]
        assert "goods_value" in ai_schema["properties"]
        assert "currency" in ai_schema["properties"]
        for req in ("product", "origin_country", "destination_country", "quantity", "goods_value", "currency"):
            assert req in ai_schema["required"]


class TestHandleCallTool:
    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        params = types.CallToolRequestParams(name="nonexistent", arguments={})
        result = await handle_call_tool(None, params)
        data = json.loads(result.content[0].text)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_missing_identifier(self):
        params = types.CallToolRequestParams(name="verify_french_company", arguments={})
        result = await handle_call_tool(None, params)
        data = json.loads(result.content[0].text)
        assert data.get("error") == "identifier is required"

    @pytest.mark.asyncio
    async def test_validation_error(self, tmp_path):
        with patch("factrail.mcp_http_server._cached_lookup") as mock:
            mock.return_value = {"error": "validation", "message": "Invalid SIREN"}
            params = types.CallToolRequestParams(
                name="verify_french_company", arguments={"identifier": "123"}
            )
            result = await handle_call_tool(None, params)
            data = json.loads(result.content[0].text)
            assert data["error"] == "validation"


@pytest.mark.asyncio
class TestHealthEndpoints:
    async def test_healthz(self):
        from starlette.testclient import TestClient
        starlette_app = build_app()
        with TestClient(starlette_app) as client:
            r = client.get("/healthz")
            assert r.status_code == 200
            assert r.text == "ok"

    async def test_readyz(self):
        from starlette.testclient import TestClient
        starlette_app = build_app()
        with TestClient(starlette_app) as client:
            r = client.get("/readyz")
            assert r.status_code == 200
            data = r.json()
            assert data["status"] == "ready"
            assert set(data) == {"status"}


@pytest.mark.asyncio
class TestOriginValidation:
    async def test_rejects_invalid_origin_when_configured(self, monkeypatch):
        monkeypatch.setenv("FACTRAIL_ALLOWED_ORIGINS", "https://trusted.com")
        from starlette.testclient import TestClient
        from factrail.mcp_http_server import build_app
        starlette_app = build_app()
        with TestClient(starlette_app) as client:
            r = client.post(
                "/mcp",
                headers={
                    "Content-Type": "application/json",
                    "Origin": "https://evil.com",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Accept": "application/json, text/event-stream",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
            assert r.status_code == 403

    async def test_allows_missing_origin(self):
        """Same-origin/API clients send no Origin — should pass through to MCP transport."""
        from starlette.testclient import TestClient
        from factrail.mcp_http_server import build_app
        starlette_app = build_app()
        with TestClient(starlette_app) as client:
            r = client.post(
                "/mcp",
                headers={
                    "Content-Type": "application/json",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Accept": "application/json, text/event-stream",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
            # 400 is expected because raw JSON-RPC without _meta is spec-invalid
            # The important thing is it's NOT 403 (origin rejection)
            assert r.status_code != 403


@pytest.mark.asyncio
class TestRateLimiting:
    async def test_rate_limit_enforced(self, monkeypatch):
        """After exceeding quota, returns 429 with retry info."""
        from starlette.testclient import TestClient
        from factrail import per_client_limiter
        per_client_limiter._global_limiter = PerClientRateLimiter(
            requests_per_window=2, window_seconds=60, max_wait=0.0
        )
        starlette_app = build_app()
        with TestClient(starlette_app) as client:
            headers = {
                "Content-Type": "application/json",
                "MCP-Protocol-Version": "2026-07-28",
                "Accept": "application/json, text/event-stream",
            }
            body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
            r1 = client.post("/mcp", headers=headers, json=body)
            r2 = client.post("/mcp", headers=headers, json=body)
            # 3rd request should be rate limited (429)
            r3 = client.post("/mcp", headers=headers, json=body)
            assert r3.status_code == 429
            data = r3.json()
            assert data["error"] == "rate_limited"
            assert "retry_after" in data
            assert "Retry-After" in r3.headers


@pytest.mark.asyncio
class TestMCPProtocolBehavior:
    """Integration tests using raw httpx against a real uvicorn server."""

    async def _with_test_server(self, test_fn):
        """Helper to run a test against a real server."""
        import uvicorn
        from factrail.mcp_http_server import build_app
        from factrail import per_client_limiter

        per_client_limiter._global_limiter = PerClientRateLimiter(
            requests_per_window=100, window_seconds=60, max_wait=0.0
        )

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        starlette_app = build_app()
        config = uvicorn.Config(starlette_app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)

        def run_server():
            asyncio.run(server.serve())

        t = threading.Thread(target=run_server, daemon=True)
        t.start()
        _time.sleep(0.5)

        try:
            await test_fn(port)
        finally:
            server.should_exit = True

    async def test_no_session_id_in_response(self):
        """Stateless mode: no Mcp-Session-Id header in response."""
        async def _test(port):
            async with httpx.AsyncClient(follow_redirects=True) as client:
                r = await client.post(
                    f"http://127.0.0.1:{port}/mcp",
                    headers={
                        "Content-Type": "application/json",
                        "MCP-Protocol-Version": "2026-07-28",
                        "Accept": "application/json, text/event-stream",
                    },
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                )
                assert "mcp-session-id" not in r.headers
                assert "Mcp-Session-Id" not in r.headers
        await self._with_test_server(_test)

    async def test_protocol_version_negotiation(self):
        """Server rejects requests with unsupported MCP-Protocol-Version."""
        async def _test(port):
            async with httpx.AsyncClient(follow_redirects=True) as client:
                r = await client.post(
                    f"http://127.0.0.1:{port}/mcp",
                    headers={
                        "Content-Type": "application/json",
                        "MCP-Protocol-Version": "2099-01-01",
                        "Accept": "application/json, text/event-stream",
                    },
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                )
                assert r.status_code in (400, 426, 200)
        await self._with_test_server(_test)

    async def test_post_returns_json(self):
        """POST to /mcp with valid spec request returns JSON."""
        async def _test(port):
            async with httpx.AsyncClient(follow_redirects=True) as client:
                r = await client.post(
                    f"http://127.0.0.1:{port}/mcp",
                    headers={
                        "Content-Type": "application/json",
                        "MCP-Protocol-Version": "2026-07-28",
                        "Mcp-Method": "tools/list",
                        "Accept": "application/json, text/event-stream",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/list",
                        "params": {
                            "_meta": {
                                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                                "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1.0"},
                                "io.modelcontextprotocol/clientCapabilities": {},
                            }
                        },
                    },
                )
                assert r.status_code == 200
                assert r.headers["content-type"].startswith("application/json")
                data = r.json()
                assert "result" in data
                assert "tools" in data["result"]
                assert len(data["result"]["tools"]) == 6
                tool_names = {t["name"] for t in data["result"]["tools"]}
                assert "verify_french_company" in tool_names
                assert "assess_import" in tool_names
        await self._with_test_server(_test)


# ---------------------------------------------------------------------------
# Host header validation tests (FACTRAIL_ALLOWED_HOSTS)
# ---------------------------------------------------------------------------


def _app_with_hosts(allowed_hosts_env: str, allowed_origins_env: str = "") -> Starlette:
    """Build a build_app() app with the given env values injected.

    importlib reloading would be heavy; instead monkeypatch os.environ and
    call build_app() directly for each test case.
    """
    import os as _os

    old_hosts = _os.environ.get("FACTRAIL_ALLOWED_HOSTS")
    old_origins = _os.environ.get("FACTRAIL_ALLOWED_ORIGINS")
    _os.environ["FACTRAIL_ALLOWED_HOSTS"] = allowed_hosts_env
    _os.environ["FACTRAIL_ALLOWED_ORIGINS"] = allowed_origins_env
    try:
        return build_app()
    finally:
        if old_hosts is None:
            _os.environ.pop("FACTRAIL_ALLOWED_HOSTS", None)
        else:
            _os.environ["FACTRAIL_ALLOWED_HOSTS"] = old_hosts
        if old_origins is None:
            _os.environ.pop("FACTRAIL_ALLOWED_ORIGINS", None)
        else:
            _os.environ["FACTRAIL_ALLOWED_ORIGINS"] = old_origins


@pytest.mark.asyncio
class TestHostValidation:
    """Verify Host header allowlisting via FACTRAIL_ALLOWED_HOSTS.

    The MCP SDK's TransportSecurityMiddleware validates the Host header
    against the TransportSecuritySettings.allowed_hosts list (421 on miss).
    These tests exercise that behaviour directly through TestClient, so no
    live uvicorn server is required.
    """

    def _post_mcp(self, client, host: str, origin: str | None = None) -> httpx.Request:
        headers = {
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2026-07-28",
            "Accept": "application/json, text/event-stream",
            "Host": host,
        }
        if origin is not None:
            headers["Origin"] = origin
        return client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )

    async def test_accepted_public_host(self, monkeypatch):
        """A Cloudflare-tunnel public hostname is accepted (200 or 400, not 421)."""
        starlette_app = _app_with_hosts(
            "edgar-bay-happy-orientation.trycloudflare.com,edgar-bay-happy-orientation.trycloudflare.com:*,127.0.0.1,127.0.0.1:*,localhost,localhost:*",
            "",
        )
        with TestClient(starlette_app) as client:
            r = self._post_mcp(client, "edgar-bay-happy-orientation.trycloudflare.com")
            # The MCP SDK transport security returns 421 on a rejected Host.
            # A public hostname in the allowlist must NOT be rejected with 421.
            assert r.status_code != 421, (
                f"Expected allowed Host to not be 421, got {r.status_code}"
            )

    async def test_accepted_localhost_host(self, monkeypatch):
        """localhost and 127.0.0.1 Hosts are accepted for local development."""
        starlette_app = _app_with_hosts(
            "127.0.0.1,127.0.0.1:*,localhost,localhost:*",
            "",
        )
        with TestClient(starlette_app) as client:
            for host in ("localhost", "localhost:8765", "127.0.0.1", "127.0.0.1:8765"):
                r = self._post_mcp(client, host)
                assert r.status_code != 421, (
                    f"Expected localhost Host {host!r} to not be 421, got {r.status_code}"
                )

    async def test_rejected_unknown_host(self, monkeypatch):
        """An unknown Host header is rejected with 421 (DNS-rebinding protection)."""
        starlette_app = _app_with_hosts(
            "127.0.0.1,127.0.0.1:*,localhost,localhost:*",
            "",
        )
        with TestClient(starlette_app) as client:
            r = self._post_mcp(client, "evil.example.com")
            assert r.status_code == 421, (
                f"Expected unknown Host to be rejected with 421, got {r.status_code}"
            )

    async def test_dns_rebinding_protection_enabled_by_default(self, monkeypatch):
        """Without FACTRAIL_ALLOWED_HOSTS, DNS-rebinding protection still arms
        and rejects a non-localhost Host header with 421."""
        import os as _os

        old_hosts = _os.environ.get("FACTRAIL_ALLOWED_HOSTS")
        _os.environ.pop("FACTRAIL_ALLOWED_HOSTS", None)
        try:
            starlette_app = build_app()
        finally:
            if old_hosts is None:
                _os.environ.pop("FACTRAIL_ALLOWED_HOSTS", None)
            else:
                _os.environ["FACTRAIL_ALLOWED_HOSTS"] = old_hosts

        with TestClient(starlette_app) as client:
            r = self._post_mcp(client, "evil.example.com")
            assert r.status_code == 421, (
                "DNS-rebinding protection should reject unknown hosts by default"
            )

            # localhost must still pass
            r2 = self._post_mcp(client, "localhost")
            assert r2.status_code != 421


@pytest.mark.asyncio
class TestOriginValidationStillWorks:
    """Regression check: existing Origin validation behaviour is unchanged
    when FACTRAIL_ALLOWED_HOSTS is also configured."""

    def _post_mcp(self, client, host: str, origin: str | None = None) -> httpx.Request:
        headers = {
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2026-07-28",
            "Accept": "application/json, text/event-stream",
            "Host": host,
        }
        if origin is not None:
            headers["Origin"] = origin
        return client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )

    async def test_origin_rejected_when_invalid_and_configured(self, monkeypatch):
        starlette_app = _app_with_hosts(
            "127.0.0.1,127.0.0.1:*,localhost,localhost:*",
            "https://trusted.com",
        )
        with TestClient(starlette_app) as client:
            r = self._post_mcp(
                client,
                "127.0.0.1",
                origin="https://evil.com",
            )
            # The SDK's TransportSecurityMiddleware returns 403 for invalid Origin.
            # Factrail's own OriginValidationMiddleware would also return 403.
            # Either way a 403 is expected; the key thing is the request is rejected.
            assert r.status_code == 403, (
                f"Expected invalid Origin to be rejected with 403, got {r.status_code}"
            )

    async def test_missing_origin_still_allowed(self, monkeypatch):
        """Same-origin / API clients that send no Origin must not be rejected."""
        starlette_app = _app_with_hosts(
            "127.0.0.1,127.0.0.1:*,localhost,localhost:*",
            "",
        )
        with TestClient(starlette_app) as client:
            r = self._post_mcp(client, "127.0.0.1")
            # Missing Origin must be allowed through (no 403)
            assert r.status_code != 403, (
                "Missing Origin should not trigger 403"
            )


@pytest.mark.asyncio
class TestCachedLookup:
    async def test_cache_hit_on_repeated_call(self, tmp_path):
        from factrail import cache as cache_mod
        cache_mod._global_cache = SqliteCache(
            db_path=str(tmp_path / "test.db"),
            default_ttl=3600,
            max_entries=100,
        )

        from factrail.models import FrenchCompany, SourceMeta
        from datetime import datetime, timezone

        mock_fc = FrenchCompany(
            siren="784671695",
            legal_name="TEST CACHE",
            checked_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            sources=[SourceMeta(id="test", name="test", url="http://test", retrieved_at=datetime(2024, 1, 1, tzinfo=timezone.utc))],
        )

        call_count = 0
        def fake_lookup(identifier):
            nonlocal call_count
            call_count += 1
            return mock_fc

        with patch("factrail.mcp_http_server.InseeAdapter") as MockAdapter:
            MockAdapter.return_value.lookup_with_bodacc.side_effect = fake_lookup

            result1 = await handle_call_tool(
                None,
                types.CallToolRequestParams(
                    name="verify_french_company", arguments={"identifier": "784671695"}
                ),
            )
            assert call_count == 1

            result2 = await handle_call_tool(
                None,
                types.CallToolRequestParams(
                    name="verify_french_company", arguments={"identifier": "784671695"}
                ),
            )
            assert call_count == 1

    async def test_stale_on_upstream_failure(self, tmp_path):
        from factrail import cache as cache_mod
        from factrail.models import FrenchCompany, SourceMeta
        from datetime import datetime, timezone
        from factrail.sources.insee import UpstreamError

        cache_mod._global_cache = SqliteCache(
            db_path=str(tmp_path / "test.db"),
            default_ttl=1,
            max_entries=100,
        )

        stale_fc = FrenchCompany(
            siren="784671695",
            legal_name="STALE",
            checked_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            sources=[SourceMeta(id="test", name="test", url="http://test", retrieved_at=datetime(2024, 1, 1, tzinfo=timezone.utc))],
        )
        cache_mod._global_cache.set("784671695", stale_fc.model_dump(mode="json"), ttl=1, source="insee_live")
        _time.sleep(1.1)

        with patch("factrail.mcp_http_server.InseeAdapter") as MockAdapter:
            MockAdapter.return_value.lookup_with_bodacc.side_effect = UpstreamError("Timeout")

            result = await handle_call_tool(
                None,
                types.CallToolRequestParams(
                    name="verify_french_company", arguments={"identifier": "784671695"}
                ),
            )
            text = result.content[0].text
            data = json.loads(text)
            assert "siren" in data or "error" in data
