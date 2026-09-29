"""FACTRAIL 2.4.1: tunnel-aware rate limiting, wildcard Origin, tool metadata, server card."""
from __future__ import annotations

import time

import mcp.types as types
import pytest
from starlette.testclient import TestClient

from factrail import __version__, per_client_limiter
from factrail.mcp_http_server import build_app, handle_call_tool, handle_list_tools
from factrail.per_client_limiter import (
    PerClientRateLimiter,
    detect_gateway,
    gateway_limiter_from_env,
    limiter_from_env,
    resolve_client,
)
from factrail.tool_catalog import LEGACY_TOOL_NAMES

MCP_HEADERS = {
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2026-07-28",
    "Accept": "application/json, text/event-stream",
}
BODY = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
SMITHERY_UA = "SmitheryBot/1.0 (+https://smithery.ai)"


@pytest.fixture
def limiters():
    old = (per_client_limiter._global_limiter, per_client_limiter._gateway_limiter)
    direct = PerClientRateLimiter(requests_per_window=2, window_seconds=60, block_excessive_after=0)
    gateway = PerClientRateLimiter(requests_per_window=5, window_seconds=60, block_excessive_after=0)
    per_client_limiter._global_limiter, per_client_limiter._gateway_limiter = direct, gateway
    yield direct, gateway
    per_client_limiter._global_limiter, per_client_limiter._gateway_limiter = old


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("FACTRAIL_ALLOWED_ORIGINS", "FACTRAIL_ALLOWED_HOSTS", "FACTRAIL_HIDE_LEGACY_TOOLS",
                 "FACTRAIL_GATEWAY_CLIENT_HEADER", "FACTRAIL_GATEWAY_USER_AGENTS"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


# --------------------------------------------------------------------------- limiter

def test_limiter_rejects_immediately_without_sleeping():
    limiter = PerClientRateLimiter(requests_per_window=1, window_seconds=1, max_wait=5.0)
    assert limiter.check("a")["allowed"]
    start = time.monotonic()
    result = limiter.check("a")
    assert time.monotonic() - start < 0.05
    assert result["allowed"] is False and 0 < result["retry_after"] <= 1.0


def test_limiter_blocks_after_repeated_rejections():
    limiter = PerClientRateLimiter(requests_per_window=1, window_seconds=60,
                                   block_excessive_after=3, block_duration=120)
    limiter.check("a")
    results = [limiter.check("a") for _ in range(3)]
    assert results[-1]["retry_after"] >= 119
    assert limiter.check("a")["retry_after"] >= 119
    assert limiter.check("b")["allowed"]


def test_limiter_disabled_when_limit_zero():
    limiter = PerClientRateLimiter(requests_per_window=0)
    assert all(limiter.check("a")["allowed"] for _ in range(1000))


def test_limiter_env_defaults_and_overrides(monkeypatch):
    for name in ("FACTRAIL_RATE_LIMIT_PER_MIN", "FACTRAIL_RATE_WINDOW_SECONDS", "FACTRAIL_RATE_BLOCK_AFTER",
                 "FACTRAIL_RATE_BLOCK_SECONDS", "FACTRAIL_GATEWAY_RATE_LIMIT_PER_MIN",
                 "FACTRAIL_GATEWAY_RATE_BLOCK_AFTER"):
        monkeypatch.delenv(name, raising=False)
    d, g = limiter_from_env(), gateway_limiter_from_env()
    assert (d.requests_per_window, d.window_seconds, d.block_excessive_after, d.block_duration) == (60, 60, 30, 300)
    assert (g.requests_per_window, g.window_seconds, g.block_excessive_after) == (600, 60, 0)
    monkeypatch.setenv("FACTRAIL_RATE_LIMIT_PER_MIN", "120")
    monkeypatch.setenv("FACTRAIL_RATE_WINDOW_SECONDS", "30")
    monkeypatch.setenv("FACTRAIL_RATE_BLOCK_AFTER", "0")
    monkeypatch.setenv("FACTRAIL_RATE_BLOCK_SECONDS", "60")
    monkeypatch.setenv("FACTRAIL_GATEWAY_RATE_LIMIT_PER_MIN", "bogus")
    d, g = limiter_from_env(), gateway_limiter_from_env()
    assert (d.requests_per_window, d.window_seconds, d.block_excessive_after, d.block_duration) == (120, 30, 0, 60)
    assert g.requests_per_window == 600 and g.window_seconds == 30


# --------------------------------------------------------------------------- client identity

def test_resolve_client_trusts_forwarding_headers_only_from_loopback(clean_env):
    assert resolve_client("127.0.0.1", {"cf-connecting-ip": "203.0.113.7"}).key == "ip:203.0.113.7"
    assert resolve_client("::1", {"x-forwarded-for": "198.51.100.2, 10.0.0.1"}).key == "ip:198.51.100.2"
    assert resolve_client("127.0.0.1", {"cf-connecting-ip": "2001:db8::1", "x-forwarded-for": "198.51.100.2"}).key == "ip:2001:db8::1"
    assert resolve_client("198.51.100.9", {"cf-connecting-ip": "203.0.113.7"}).key == "ip:198.51.100.9"
    assert resolve_client("127.0.0.1", {"cf-connecting-ip": "not-an-ip"}).key == "ip:127.0.0.1"
    assert resolve_client("127.0.0.1", {}).key == "ip:127.0.0.1"
    assert resolve_client(None, {}).key == "ip:unknown"


def test_smithery_detection(clean_env):
    assert detect_gateway({"user-agent": SMITHERY_UA}) == "smithery"
    assert detect_gateway({"user-agent": "x", "cf-worker": "smithery.ai"}) == "smithery"
    assert detect_gateway({"user-agent": "Claude-User/1.0"}) is None
    ident = resolve_client("127.0.0.1", {"user-agent": SMITHERY_UA, "cf-connecting-ip": "2a06:98c0:3600::103"})
    assert (ident.kind, ident.key) == ("gateway", "gateway:smithery")
    clean_env.setenv("FACTRAIL_GATEWAY_CLIENT_HEADER", "X-End-User")
    ident = resolve_client("127.0.0.1", {"user-agent": SMITHERY_UA, "x-end-user": "u-42"})
    assert ident.key == "gateway:smithery:u-42"
    clean_env.setenv("FACTRAIL_GATEWAY_USER_AGENTS", "")
    assert detect_gateway({"user-agent": SMITHERY_UA}) is None


def test_middleware_buckets_per_cf_ip_and_gateway(clean_env, limiters):
    with TestClient(build_app(), client=("127.0.0.1", 40000)) as client:
        post = lambda **h: client.post("/mcp", headers={**MCP_HEADERS, **h}, json=BODY)
        for _ in range(2):
            assert post(**{"CF-Connecting-IP": "203.0.113.1"}).status_code != 429
        r = post(**{"CF-Connecting-IP": "203.0.113.1"})
        assert r.status_code == 429
        assert r.json()["error"] == "rate_limited" and r.json()["limit"] == 2
        assert int(r.headers["Retry-After"]) >= 1
        # Another end user behind the same tunnel has its own bucket.
        assert post(**{"CF-Connecting-IP": "203.0.113.2"}).status_code != 429
        # Smithery gateway traffic uses the separate, larger gateway bucket.
        for _ in range(5):
            assert post(**{"User-Agent": SMITHERY_UA, "CF-Connecting-IP": "203.0.113.1"}).status_code != 429
        assert post(**{"User-Agent": SMITHERY_UA}).status_code == 429


def test_middleware_ignores_forwarding_headers_from_untrusted_peer(clean_env, limiters):
    with TestClient(build_app(), client=("198.51.100.50", 40000)) as client:
        codes = [client.post("/mcp", headers={**MCP_HEADERS, "CF-Connecting-IP": f"203.0.113.{i}"}, json=BODY).status_code
                 for i in range(3)]
        assert codes[-1] == 429


# --------------------------------------------------------------------------- Origin

def _post(client, host="127.0.0.1", origin=None):
    headers = {**MCP_HEADERS, "Host": host}
    if origin:
        headers["Origin"] = origin
    return client.post("/mcp", headers=headers, json=BODY)


def test_origin_default_unchanged_rejects_foreign_origin(clean_env, limiters):
    limiters[0].requests_per_window = 100
    with TestClient(build_app()) as client:
        assert _post(client, origin="https://example.com").status_code == 403
        assert _post(client).status_code not in (403, 421)
        assert _post(client, host="evil.example.com").status_code == 421


def test_origin_wildcard_allows_any_origin_but_keeps_host_check(clean_env, limiters):
    limiters[0].requests_per_window = 100
    clean_env.setenv("FACTRAIL_ALLOWED_ORIGINS", "*")
    clean_env.setenv("FACTRAIL_ALLOWED_HOSTS", "mcp.factrail.online,127.0.0.1")
    with TestClient(build_app()) as client:
        r = _post(client, host="mcp.factrail.online", origin="https://example.com")
        assert r.status_code not in (403, 421)
        assert r.headers.get("access-control-allow-origin") == "*"
        assert _post(client, host="mcp.factrail.online").status_code not in (403, 421)
        assert _post(client, host="evil.example.com", origin="https://example.com").status_code == 421
        assert _post(client, host="evil.example.com").status_code == 421
        pre = client.options("/mcp", headers={"Host": "mcp.factrail.online", "Origin": "https://example.com",
                                              "Access-Control-Request-Method": "POST",
                                              "Access-Control-Request-Headers": "content-type,mcp-protocol-version"})
        assert pre.status_code == 200
        assert pre.headers["access-control-allow-origin"] == "*"


def test_explicit_origin_list_still_enforced(clean_env, limiters):
    limiters[0].requests_per_window = 100
    clean_env.setenv("FACTRAIL_ALLOWED_ORIGINS", "https://trusted.example")
    with TestClient(build_app()) as client:
        assert _post(client, origin="https://evil.example").status_code == 403
        assert _post(client, origin="https://trusted.example").status_code not in (403, 421)


# --------------------------------------------------------------------------- tool metadata

@pytest.mark.asyncio
async def test_every_tool_has_title_use_when_and_annotations(clean_env):
    tools = (await handle_list_tools(None, None)).tools
    assert len(tools) == 7
    for tool in tools:
        assert tool.title and tool.annotations.title == tool.title
        ann = tool.annotations
        assert ann.read_only_hint is True and ann.destructive_hint is False and ann.idempotent_hint is True
        assert ann.open_world_hint is not None
        if tool.name in LEGACY_TOOL_NAMES:
            assert tool.title.startswith("[Deprecated]")
            assert tool.description.startswith("Deprecated: use factrail_")
            assert "Use when" in tool.description or "Use only when" in tool.description
        else:
            assert tool.description.startswith("Use when ")


@pytest.mark.asyncio
async def test_hide_legacy_tools_switch_keeps_them_callable(clean_env, tmp_path):
    clean_env.setenv("FACTRAIL_CACHE_PATH", str(tmp_path / "db.sqlite"))
    clean_env.setenv("FACTRAIL_HIDE_LEGACY_TOOLS", "1")
    names = {t.name for t in (await handle_list_tools(None, None)).tools}
    assert names == {"factrail_capabilities", "factrail_verify", "factrail_assess", "factrail_get_receipt"}
    from factrail.mcp_server import handle_list_tools as stdio_list
    assert {t.name for t in (await stdio_list(None, None)).tools} == names
    result = await handle_call_tool(None, types.CallToolRequestParams(name="analyze_company", arguments={"company_name": "X"}))
    assert not result.is_error


@pytest.mark.asyncio
async def test_stdio_tools_match_http_tools(clean_env):
    from factrail.mcp_server import handle_list_tools as stdio_list
    http = {t.name: t for t in (await handle_list_tools(None, None)).tools}
    stdio = {t.name: t for t in (await stdio_list(None, None)).tools}
    assert http == stdio


# --------------------------------------------------------------------------- server card

def test_server_card_route(clean_env, limiters):
    with TestClient(build_app()) as client:
        r = client.get("/.well-known/mcp/server-card.json")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/json")
        assert r.headers["access-control-allow-origin"] == "*"
        card = r.json()
        assert card["title"] == "FACTRAIL" and card["version"] == __version__ == "2.4.1"
        assert card["serverInfo"]["version"] == __version__
        assert card["homepage"] == "https://factrail.online/"
        assert card["endpoint"] == "https://mcp.factrail.online/mcp"
        assert card["transport"]["type"] == "streamable-http"
        assert card["remotes"][0]["url"] == card["endpoint"]
        assert card["authentication"]["required"] is False
        assert card["description"].startswith("FACTRAIL — Evidence infrastructure for AI agents.")
        assert {t["name"] for t in card["tools"]} >= {"factrail_verify", "factrail_assess"}
        assert all(t["title"] and t["description"] and t["inputSchema"] for t in card["tools"])
        assert client.get("/.well-known/glama.json").json()["claim"].startswith("glama_claim_")


def test_server_card_not_host_restricted_or_rate_limited(clean_env, limiters):
    limiters[0].requests_per_window = 1
    with TestClient(build_app()) as client:
        for _ in range(3):
            assert client.get("/.well-known/mcp/server-card.json", headers={"Host": "mcp.factrail.online"}).status_code == 200


@pytest.mark.asyncio
async def test_server_card_tools_match_tools_list(clean_env):
    from factrail.server_card import build_server_card
    listed = {t.name: t for t in (await handle_list_tools(None, None)).tools}
    card = {t["name"]: t for t in build_server_card()["tools"]}
    assert card.keys() == listed.keys()
    for name, entry in card.items():
        assert entry["title"] == listed[name].title
        assert entry["description"] == listed[name].description
        assert entry["inputSchema"] == listed[name].input_schema
        assert entry["annotations"]["readOnlyHint"] is True
