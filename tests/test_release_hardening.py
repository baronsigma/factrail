"""Offline release and public-boundary checks for FACTRAIL 2.2.0."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
import tomllib

import mcp.types as types
import pytest
from starlette.testclient import TestClient

from factrail.cache import SqliteCache
from factrail.evidence.demand import DemandStore
from factrail.evidence.receipts import ReceiptRepository, receipt_id_for
from factrail.mcp_http_server import build_app, handle_call_tool, handle_list_tools
from factrail.per_client_limiter import PerClientRateLimiter
from tests.test_evidence import NOW, sample_envelope


@pytest.mark.asyncio
async def test_release_and_contract_versions_and_manifest():
    root = Path(__file__).resolve().parents[1]
    package = tomllib.loads((root / "pyproject.toml").read_text())
    manifest = json.loads((root / "server.json").read_text())
    from factrail.mcp_http_server import app as http_app
    from factrail.mcp_server import app as stdio_app
    assert package["project"]["version"] == manifest["version"] == http_app.version == stdio_app.version == "2.4.0"
    assert sample_envelope().schema_version == "1.2"
    runtime = {tool.name: tool for tool in (await handle_list_tools(None, None)).tools}
    for tool in manifest["tools"]:
        assert tool["input_schema"] == runtime[tool["name"]].input_schema
        if tool["name"].startswith("factrail_"):
            assert tool["output_schema"] == runtime[tool["name"]].output_schema


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt_id", ["", "fr_missing", "fr_" + "A" * 64,
                                      "../../secrets", "' OR 1=1 --", "fr_" + "g" * 64])
async def test_receipt_rejects_malformed_ids(receipt_id, tmp_path, monkeypatch):
    monkeypatch.setenv("FACTRAIL_CACHE_PATH", str(tmp_path / "receipts.db"))
    result = await handle_call_tool(None, types.CallToolRequestParams(
        name="factrail_get_receipt", arguments={"receipt_id": receipt_id}))
    assert result.is_error
    assert json.loads(result.content[0].text)["error"] == "invalid_input"
    with pytest.raises(ValueError):
        ReceiptRepository(str(tmp_path / "receipts.db")).get(receipt_id)


@pytest.mark.asyncio
async def test_receipt_unknown_valid_id_is_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTRAIL_CACHE_PATH", str(tmp_path / "receipts.db"))
    result = await handle_call_tool(None, types.CallToolRequestParams(
        name="factrail_get_receipt", arguments={"receipt_id": "fr_" + "0" * 64}))
    assert result.is_error
    assert json.loads(result.content[0].text)["error"] == "not_found"
    result = await handle_call_tool(None, types.CallToolRequestParams(
        name="factrail_get_receipt", arguments={"receipt_id": "fr_" + "0" * 64, "table": "cache"}))
    assert json.loads(result.content[0].text)["error"] == "invalid_input"


@pytest.mark.asyncio
async def test_strategic_schemas_and_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTRAIL_CACHE_PATH", str(tmp_path / "demand.db"))
    tools = {t.name: t for t in (await handle_list_tools(None, None)).tools}
    assert len(tools) == 7
    for name in ("factrail_verify", "factrail_assess", "factrail_get_receipt"):
        assert "state_fingerprint" in tools[name].output_schema["properties"]
        assert tools[name].input_schema["additionalProperties"] is False
    assert tools["factrail_verify"].input_schema["properties"]["subject_type"]["enum"] == ["company_fr"]
    assert tools["factrail_verify"].input_schema["properties"]["fields"]["minItems"] == 1
    assert tools["factrail_assess"].input_schema["properties"]["assessment_type"]["enum"] == ["import"]
    assert set(tools["factrail_assess"].input_schema["properties"]["parameters"]["required"]) == {
        "product", "origin_country", "destination_country", "quantity", "goods_value", "currency"}
    assert tools["factrail_assess"].input_schema["properties"]["parameters"]["additionalProperties"] is False
    call = lambda name, arguments: handle_call_tool(None, types.CallToolRequestParams(name=name, arguments=arguments))
    for args in (
        {"subject_type": "company_fr", "identifier": "784671695", "fields": []},
        {"subject_type": "company_fr", "identifier": "784671695", "extra": "x"},
        {"subject_type": "company_fr", "identifier": "784671695", "fields": ["unsupported"]},
    ):
        assert (await call("factrail_verify", args)).is_error
    assert json.loads((await call("factrail_verify", {"subject_type": "vat", "identifier": "123"})).content[0].text)["error"] == "unsupported_capability"
    assert json.loads((await call("factrail_assess", {"assessment_type": "vat", "parameters": {}})).content[0].text)["error"] == "unsupported_capability"
    assert json.loads((await call("factrail_assess", {"parameters": {}})).content[0].text)["error"] == "invalid_input"
    assert json.loads((await call("factrail_assess", {"assessment_type": "import", "parameters": {}, "extra": "x"})).content[0].text)["error"] == "invalid_input"
    assert json.loads((await call("factrail_assess", {"assessment_type": "import", "parameters": {"extra": "x"}})).content[0].text)["error"] == "invalid_input"


@pytest.mark.parametrize("tool_name", ["factrail_verify", "factrail_assess", "factrail_get_receipt"])
def test_new_tools_use_http_rate_limit(tool_name, monkeypatch):
    from factrail import per_client_limiter
    monkeypatch.setattr(per_client_limiter, "_global_limiter", PerClientRateLimiter(
        requests_per_window=1, window_seconds=60, max_wait=0.0))
    with TestClient(build_app()) as client:
        headers = {"Content-Type": "application/json", "MCP-Protocol-Version": "2026-07-28",
                   "Accept": "application/json, text/event-stream"}
        client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        response = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2,
            "method": "tools/call", "params": {"name": tool_name, "arguments": {}}})
        assert response.status_code == 429
        assert response.json()["error"] == "rate_limited"


def test_readiness_is_local_and_does_not_expose_paths(tmp_path, monkeypatch):
    from factrail import cache as cache_module
    path = str(tmp_path / "private-path.db")
    monkeypatch.setattr(cache_module, "_global_cache", SqliteCache(db_path=path))
    with TestClient(build_app()) as client:
        assert client.get("/healthz").text == "ok"
        response = client.get("/readyz")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}
        assert path not in response.text


def test_readiness_failure_is_bounded(monkeypatch):
    def broken():
        raise OSError("private-db-location")
    monkeypatch.setattr("factrail.mcp_http_server.get_cache", broken)
    with TestClient(build_app()) as client:
        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready"}
        assert "private-db-location" not in response.text
        assert client.get("/healthz").status_code == 200


def test_startup_migration_is_idempotent_and_preserves_data(tmp_path):
    path = str(tmp_path / "upgrade.db")
    old = sample_envelope().model_copy(update={"schema_version": "1.0"})
    old_id = receipt_id_for(old)
    payload = old.model_dump(mode="json")
    payload["receipt_id"] = old_id
    payload.pop("state_fingerprint")
    for fact in payload["facts"]:
        fact.pop("provenance_type")
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE cache (key TEXT PRIMARY KEY, value TEXT NOT NULL,
            expires_at REAL NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1, source TEXT)""")
        conn.execute("INSERT INTO cache VALUES (?,?,?,?,?,?,?)", ("old-key", "{}", 1, 1, 1, 1, "old"))
        conn.execute("""CREATE TABLE evidence_receipts (receipt_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL,
            envelope TEXT NOT NULL, created_at TEXT NOT NULL, subject_type TEXT NOT NULL, primary_identifier TEXT)""")
        conn.execute("INSERT INTO evidence_receipts VALUES (?,?,?,?,?,?)",
                     (old_id, "1.0", json.dumps(payload), NOW.isoformat(), "company_fr", "784671695"))
        conn.execute("""CREATE TABLE evidence_demand (id INTEGER PRIMARY KEY, timestamp REAL NOT NULL,
            operation TEXT NOT NULL, capability TEXT NOT NULL, requested_fields TEXT NOT NULL,
            outcome TEXT NOT NULL, coverage TEXT, unresolved_fields TEXT NOT NULL,
            unsupported_capability INTEGER NOT NULL, source_failures TEXT NOT NULL,
            latency_ms REAL NOT NULL, cache_status TEXT)""")
        conn.execute("INSERT INTO evidence_demand VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (1, 9999999999, "verify", "company_fr", "[]", "supported", "sufficient", "[]", 0, "[]", 1.0, None))
        # Simulate a pre-versioned cache DB; initialization must not drop its row.
        conn.execute("PRAGMA user_version = 0")
    for _ in range(3):
        cache = SqliteCache(db_path=path)
        repo = ReceiptRepository(path)
        demand = DemandStore(path)
        assert repo.get(old_id).state_fingerprint.startswith("fs_")
        assert demand.report()["total_calls"] == 1
        cache.close()
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM evidence_receipts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM evidence_demand").fetchone()[0] == 1
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1


def test_demand_report_with_missing_db_or_table(tmp_path):
    missing = tmp_path / "missing.db"
    assert DemandStore(str(missing)).report()["total_calls"] == 0
    legacy = tmp_path / "legacy.db"
    with sqlite3.connect(legacy) as conn:
        conn.execute("CREATE TABLE unrelated (value TEXT)")
    assert DemandStore(str(legacy)).report()["total_calls"] == 0
    env = {**os.environ, "FACTRAIL_CACHE_PATH": str(tmp_path / "cli.db")}
    run = subprocess.run([sys.executable, "-m", "factrail.evidence.demand", "--days", "7"],
                         env=env, text=True, capture_output=True, check=True)
    assert json.loads(run.stdout)["total_calls"] == 0


def test_demand_row_excludes_raw_content(tmp_path):
    path = str(tmp_path / "telemetry.db")
    secret = "privatecompany123456789.example.com"
    DemandStore(path).record(operation=secret, capability=secret,
        requested_fields=[secret, "status"], outcome=secret, coverage=secret,
        unresolved_fields=[secret], source_failures=[secret], cache_status=secret)
    with sqlite3.connect(path) as conn:
        stored = repr(conn.execute("SELECT * FROM evidence_demand").fetchall())
    assert secret not in stored
    assert "123456789" not in stored
    assert "example.com" not in stored
    assert "status" in stored
    assert DemandStore(path).report()["total_calls"] == 1
