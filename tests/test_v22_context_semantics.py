from __future__ import annotations

import asyncio
import sqlite3
import time
from types import SimpleNamespace

import mcp.types as types

from factrail.evidence.demand import DemandStore
from factrail.evidence.receipts import ReceiptRepository
from factrail.evidence.trade import assess_import_evidence
from factrail.request_context import bind_request_context, current_request_context, from_mcp_context, reset_request_context
from factrail.trade.models import AssessImportInput
from factrail.mcp_http_server import handle_call_tool


def context(name=None, version="1.2.3", headers=None, request_id="rpc-7"):
    client_info = types.Implementation(name=name, version=version) if name else None
    params = None
    if client_info:
        params = types.InitializeRequestParams(protocolVersion="2025-11-25", capabilities={}, clientInfo=client_info)
    request = SimpleNamespace(headers=headers or {}, state=SimpleNamespace(request_id="http-abc"))
    return SimpleNamespace(session=SimpleNamespace(client_params=params), request=request, request_id=request_id)


def test_request_context_uses_sdk_client_info_and_http_request_id(monkeypatch):
    monkeypatch.delenv("FACTRAIL_TEST_MODE", raising=False)
    ctx = from_mcp_context(context("Claude Code"))
    assert ctx.client_family == "claude"
    assert ctx.mcp_client_name == "claude code"
    assert ctx.mcp_client_version == "1.2.3"
    assert ctx.origin_class == "external"
    assert ctx.request_id == "http-abc"
    assert not hasattr(ctx, "raw_ip")


def test_unknown_test_and_manual_context(monkeypatch):
    monkeypatch.delenv("FACTRAIL_TEST_MODE", raising=False)
    assert from_mcp_context(context("unrecognized client")).origin_class == "unknown"
    monkeypatch.setenv("FACTRAIL_TEST_MODE", "1")
    assert from_mcp_context(context("Claude Code")).origin_class == "internal_test"
    monkeypatch.delenv("FACTRAIL_TEST_MODE", raising=False)
    assert from_mcp_context(context(headers={"x-factrail-test-origin": "manual"})).origin_class == "manual"


def test_contextvar_isolation_across_concurrent_tasks():
    async def task(name, delay):
        from factrail.request_context import RequestContext
        token = bind_request_context(RequestContext(request_id=name, origin_class=name, client_family=name))
        try:
            await asyncio.sleep(delay)
            return current_request_context().request_id, current_request_context().origin_class
        finally:
            reset_request_context(token)
    async def run():
        return await asyncio.gather(task("external", 0.01), task("manual", 0))
    assert asyncio.run(run()) == [("external", "external"), ("manual", "manual")]


def test_dispatcher_context_reaches_demand_telemetry(monkeypatch):
    captured = []
    monkeypatch.setattr("factrail.evidence.demand.record_safely", lambda **kwargs: captured.append(kwargs))
    result = asyncio.run(handle_call_tool(context("Claude Code"), types.CallToolRequestParams(
        name="factrail_assess", arguments={"assessment_type": "not_supported", "parameters": {}})))
    assert result.is_error
    assert captured[0]["origin_class"] == "external"
    assert captured[0]["client_family"] == "claude"
    assert captured[0]["mcp_client_name"] == "claude code"
    assert captured[0]["mcp_client_version"] == "1.2.3"
    assert captured[0]["request_id"] == "http-abc"


def test_demand_gaps_and_failures_follow_source_outcome(tmp_path):
    store = DemandStore(str(tmp_path / "demand.db"))
    store.record(operation="assess", capability="import", outcome="insufficient_evidence",
        unresolved_fields=["material", "authoritative_duty_rate"],
        unresolved_reasons={"material": "missing_user_input", "authoritative_duty_rate": "lookup_disabled"},
        source_failures=["tariff_registry"], source_outcomes={"tariff_registry": "lookup_disabled"},
        origin_class="external")
    store.record(operation="assess", capability="import", outcome="insufficient_evidence",
        unresolved_fields=["authoritative_duty_rate"],
        unresolved_reasons={"authoritative_duty_rate": "source_error"},
        source_failures=["tariff_registry"], source_outcomes={"tariff_registry": "source_error"},
        origin_class="unknown")
    external = store.report(origin="external")
    assert external["period_total_calls"] == 2
    assert external["unknown_calls"] == 1
    assert external["source_failures"] == {}
    assert external["actionable_gaps"] == [
        {"capability": "import", "gap": "material", "reason": "missing_user_input", "count": 1},
        {"capability": "import", "gap": "tariff_lookup", "reason": "lookup_disabled", "count": 1},
    ]
    all_rows = store.report(all_origins=True)
    assert all_rows["source_failures"] == {"tariff_registry": 1}
    assert all_rows["source_outcomes"]["tariff_registry"] == {"lookup_disabled": 1, "source_error": 1}


def test_demand_gap_categories_distinguish_support_from_fetch(tmp_path):
    store = DemandStore(str(tmp_path / "gaps.db"))
    for reason in ("missing_user_input", "unsupported_fact", "source_not_integrated", "source_unavailable", "source_error", "classification_required"):
        store.record(operation="assess", capability="import", outcome="insufficient_evidence",
            unresolved_fields=["material"], unresolved_reasons={"material": reason}, origin_class="external",
            source_outcomes={"tariff_registry": reason} if reason in {"source_not_integrated", "source_unavailable", "source_error"} else {},
            source_failures=["tariff_registry"] if reason in {"source_unavailable", "source_error"} else [])
    report = store.report(origin="external")
    for reason in ("missing_user_input", "unsupported_fact", "source_not_integrated", "source_unavailable", "source_error", "classification_required"):
        assert report["gaps_by_category"][reason]
    assert report["source_failures"] == {"tariff_registry": 2}


def test_trade_import_missing_classification_does_not_attempt_tariff(monkeypatch):
    class NeverUsedStore:
        def get_measures(self, *args, **kwargs):
            raise AssertionError("TARIC lookup should not be queried without a classified HS code")
    monkeypatch.setattr("factrail.trade.service.get_default_store", lambda: NeverUsedStore())
    params = AssessImportInput(product="unclassified widget", origin_country="CN", destination_country="FR",
        quantity=1, goods_value=10, currency="EUR")
    envelope = assess_import_evidence(params)
    assert envelope.coverage.metadata["source_outcomes"]["tariff_registry"] == "not_required"
    assert envelope.coverage.metadata["source_failures"] == []
    assert envelope.coverage.metadata["unresolved_field_reasons"]["hs_code"] == "classification_required"
    assert "base_duty_rate_pct" not in {f.field for f in envelope.facts}


def test_trade_import_attempted_failure_and_no_fabricated_rate(monkeypatch, tmp_path):
    from factrail.trade.taric_store import TaricUnavailable
    class FailedStore:
        def get_measures(self, *args, **kwargs):
            raise TaricUnavailable(hs_code="999999", origin="CN", destination="FR", reason="timeout")
    monkeypatch.setattr("factrail.trade.service.get_default_store", lambda: FailedStore())
    params = AssessImportInput(product="unclassified widget", origin_country="CN", destination_country="FR",
        quantity=1, goods_value=10, currency="EUR", known_hs_code="999999")
    envelope = assess_import_evidence(params)
    assert envelope.coverage.metadata["source_outcomes"]["tariff_registry"] == "source_error"
    assert envelope.coverage.metadata["source_failures"] == ["access2markets_secondary"]
    assert envelope.coverage.metadata["unresolved_field_reasons"]["base_duty_rate_pct"] == "source_error"
    assert "base_duty_rate_pct" not in {f.field for f in envelope.facts}
    assert all(set(f.evidence_ids) <= {s.id for s in envelope.evidence} for f in envelope.facts)
    repo = ReceiptRepository(str(tmp_path / "receipt.db"))
    saved = repo.save(envelope)
    assert repo.get(saved.receipt_id) == saved
    assert repo.get(saved.receipt_id).state_fingerprint == saved.state_fingerprint


def test_source_attempt_annotation_is_hidden_from_legacy_trade_shape():
    from datetime import datetime, timezone
    from factrail.trade.models import Evidence, ProvenanceStatus
    item = Evidence(value="source_error", status=ProvenanceStatus.UNAVAILABLE, authority="public source",
        source="tariff source attempt", url="https://example.invalid", retrieved_at=datetime.now(timezone.utc),
        source_outcome="source_error")
    assert "source_outcome" not in item.model_dump(mode="json")
    assert item.source_outcome == "source_error"


def test_legacy_source_failure_label_remains_readable_but_is_not_reclassified(tmp_path):
    path = str(tmp_path / "legacy.db")
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE evidence_demand (
            id INTEGER PRIMARY KEY, timestamp REAL NOT NULL, operation TEXT NOT NULL, capability TEXT NOT NULL,
            requested_fields TEXT NOT NULL, outcome TEXT NOT NULL, coverage TEXT, unresolved_fields TEXT NOT NULL,
            unsupported_capability INTEGER NOT NULL, source_failures TEXT NOT NULL, latency_ms REAL NOT NULL, cache_status TEXT)""")
        conn.execute("INSERT INTO evidence_demand VALUES (1, ?, 'assess', 'import', '[]', 'insufficient_evidence', NULL, '[]', 0, '[\"tariff_registry\"]', 1.0, NULL)", (time.time(),))
    report = DemandStore(path).report(all_origins=True)
    assert report["total_calls"] == 1
    assert report["source_failures"] == {}
    assert report["legacy_source_failure_labels"] == {"tariff_registry": 1}
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT source_failures FROM evidence_demand WHERE id=1").fetchone()[0] == '["tariff_registry"]'
