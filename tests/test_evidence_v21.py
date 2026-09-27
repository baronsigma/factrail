"""Deterministic v2.1 Evidence Core behavior and migration tests."""
from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

import mcp.types as types
import pytest

from factrail.evidence.authority import AuthorityClass, AuthorityRule, resolve_assertions
from factrail.evidence.demand import DemandStore
from factrail.evidence.models import Conflict, ConflictSide, EvidenceSource, Fact, VerificationStatus
from factrail.evidence.receipts import ReceiptRepository, receipt_id_for, state_fingerprint_for
from factrail.evidence.trade import assess_import_evidence, import_to_envelope
from factrail.mcp_http_server import handle_call_tool, handle_list_tools
from factrail.trade.models import AssessImportInput, ProvenanceStatus
from factrail.trade.service import assess_import
from tests.test_evidence import NOW, sample_company, sample_envelope


def import_input():
    return AssessImportInput(product="750ml insulated stainless steel bottle", origin_country="CN",
        destination_country="FR", quantity=5, goods_value=1000, currency="EUR", known_hs_code="961700")


def test_state_fingerprint_observation_and_order():
    a = sample_envelope()
    b = a.model_copy(deep=True)
    b.generated_at += timedelta(days=1)
    b.freshness.generated_at += timedelta(days=1)
    b.evidence[0].retrieved_at += timedelta(days=1)
    b.freshness.oldest_supporting_retrieved_at += timedelta(days=1)
    b.freshness.newest_supporting_retrieved_at += timedelta(days=1)
    assert state_fingerprint_for(a) == state_fingerprint_for(b)
    assert receipt_id_for(a) != receipt_id_for(b)
    a.facts.append(Fact(field="legal_name", value="Example", evidence_ids=["insee-siren"]))
    b.facts.insert(0, Fact(field="legal_name", value="Example", evidence_ids=["insee-siren"]))
    assert state_fingerprint_for(a) == state_fingerprint_for(b)
    assert state_fingerprint_for(a) == state_fingerprint_for(a)
    b.facts[0].value = "Changed"
    assert state_fingerprint_for(a) != state_fingerprint_for(b)


def test_state_fingerprint_evidence_order_and_ids():
    a = sample_envelope()
    a.evidence.append(EvidenceSource(id="other", source_type="government_registry", authority_class="official",
        publisher="Second", retrieved_at=NOW, source_status="available"))
    a.facts[0].evidence_ids = ["insee-siren", "other"]
    b = a.model_copy(deep=True)
    b.evidence.reverse()
    b.evidence[0].id = "renamed_second"
    b.evidence[1].id = "renamed_first"
    b.facts[0].evidence_ids = ["renamed_second", "renamed_first"]
    assert state_fingerprint_for(a) == state_fingerprint_for(b)


def test_conflict_state_changes_fingerprint():
    a = sample_envelope()
    a.evidence.append(EvidenceSource(id="other", source_type="registry", authority_class="official",
        publisher="Other", retrieved_at=NOW, source_status="available"))
    a.conflicts.append(Conflict(field="status", sides=[ConflictSide(value="active", evidence_ids=["insee-siren"]),
        ConflictSide(value="inactive", evidence_ids=["other"])], resolution_status="unresolved"))
    b = a.model_copy(deep=True)
    b.conflicts[0].resolution_status = "resolved"
    b.conflicts[0].resolution_reason = "policy"
    assert state_fingerprint_for(a) != state_fingerprint_for(b)


def test_v20_receipt_migration(tmp_path):
    path = str(tmp_path / "old.db")
    old = sample_envelope().model_copy(update={"schema_version": "1.0"})
    expected_id = receipt_id_for(old)
    old_json = old.model_dump(mode="json")
    old_json["receipt_id"] = expected_id
    old_json.pop("state_fingerprint")
    for fact in old_json["facts"]:
        fact.pop("provenance_type")
    repo = ReceiptRepository(path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO evidence_receipts VALUES (?,?,?,?,?,?)",
            (expected_id, "1.0", json.dumps(old_json), NOW.isoformat(), "company_fr", "784671695"))
        conn.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY)")
    restored = ReceiptRepository(path).get(expected_id)
    assert restored.receipt_id == expected_id
    assert restored.state_fingerprint == state_fingerprint_for(restored)
    assert receipt_id_for(restored) == expected_id
    assert sqlite3.connect(path).execute("SELECT COUNT(*) FROM cache").fetchone()[0] == 0
    assert DemandStore(path).report()["total_calls"] == 0


def test_authority_policy_and_unresolved_conflict():
    sources = [EvidenceSource(id="registry", source_type="government_registry",
        authority_class=AuthorityClass.PRIMARY_OFFICIAL_REGISTRY, publisher="Registry", retrieved_at=NOW, source_status="available"),
        EvidenceSource(id="publication", source_type="government_bulletin",
        authority_class=AuthorityClass.OFFICIAL_PUBLICATION, publisher="Bulletin", retrieved_at=NOW, source_status="available")]
    facts = [Fact(field="status", value="active", evidence_ids=["registry"], jurisdiction="FR"),
             Fact(field="status", value="inactive", evidence_ids=["publication"], jurisdiction="FR")]
    unresolved = resolve_assertions(facts, sources, domain="company_fr", field="status", jurisdiction="FR")
    assert unresolved.status == VerificationStatus.CONFLICTING_SOURCES
    assert unresolved.conflict.resolution_status == "unresolved"
    rule = AuthorityRule(policy_id="current_registry", domain="company_fr", field="status", jurisdiction="FR",
        precedence=[AuthorityClass.PRIMARY_OFFICIAL_REGISTRY, AuthorityClass.OFFICIAL_PUBLICATION],
        reason="Current registry outranks historical publication for current status")
    resolved = resolve_assertions(facts, sources, domain="company_fr", field="status", jurisdiction="FR", rules=[rule])
    assert resolved.selected.value == "active"
    assert resolved.conflict.resolution_status == "resolved"
    assert "current_registry" in resolved.conflict.resolution_reason
    assert {ref for side in resolved.conflict.sides for ref in side.evidence_ids} == {"registry", "publication"}
    assert resolve_assertions(facts, sources, domain="trade", field="status", jurisdiction="FR", rules=[rule]).status == VerificationStatus.CONFLICTING_SOURCES


def test_trade_envelope_and_receipt(tmp_path):
    parameters = import_input()
    envelope = assess_import_evidence(parameters)
    assert envelope.subject.type == "import"
    assert {"hs_code", "base_duty_rate_pct", "import_vat_rate_pct", "estimated_total"} <= {f.field for f in envelope.facts}
    assert any(f.provenance_type == "derived" and f.metadata.get("formula") for f in envelope.facts)
    assert all(set(f.evidence_ids) <= {s.id for s in envelope.evidence} for f in envelope.facts)
    saved = ReceiptRepository(str(tmp_path / "db.sqlite")).save(envelope)
    restored = ReceiptRepository(str(tmp_path / "db.sqlite")).get(saved.receipt_id)
    assert restored == saved
    assert saved.state_fingerprint.startswith("fs_")
    second = assess_import_evidence(parameters)
    assert state_fingerprint_for(envelope) == state_fingerprint_for(second)


def test_trade_degraded_and_incomplete():
    parameters = import_input()
    result = assess_import(parameters)
    result.customs.base_duty_rate_pct = None
    result.sources.append(result.sources[0].model_copy(update={"status": ProvenanceStatus.UNAVAILABLE, "source": "TARIC failed", "supports": ["duty"]}))
    envelope = import_to_envelope(parameters, result)
    assert "base_duty_rate_pct" in envelope.coverage.fields_unresolved
    assert any(s.source_status == "unavailable" for s in envelope.evidence)
    assert not any(f.field == "base_duty_rate_pct" for f in envelope.facts)
    with pytest.raises(Exception):
        AssessImportInput(product="x", origin_country="CN", destination_country="FR", quantity=0, goods_value=0, currency="EUR")


@pytest.mark.asyncio
async def test_assess_mcp_and_telemetry(monkeypatch, tmp_path):
    path = str(tmp_path / "v21.db")
    monkeypatch.setenv("FACTRAIL_CACHE_PATH", path)
    names = {t.name for t in (await handle_list_tools(None, None)).tools}
    assert "factrail_assess" in names
    call = lambda name, args: handle_call_tool(None, types.CallToolRequestParams(name=name, arguments=args))
    params = import_input().model_dump(mode="json")
    result = await call("factrail_assess", {"assessment_type": "import", "parameters": params})
    assert not result.is_error
    assert result.structured_content["schema_version"] == "1.1"
    assert result.structured_content["state_fingerprint"].startswith("fs_")
    receipt = await call("factrail_get_receipt", {"receipt_id": result.structured_content["receipt_id"]})
    assert receipt.structured_content == result.structured_content
    assert (await call("factrail_assess", {"assessment_type": "unsupported", "parameters": params})).is_error
    assert (await call("factrail_assess", {"assessment_type": "import", "parameters": {}})).is_error
    assert (await call("factrail_verify", {"subject_type": "unsupported", "identifier": "123"})).is_error
    report = DemandStore(path).report()
    assert report["total_calls"] == 4
    assert report["unsupported_capabilities"]
    assert report["source_failures"].get("tariff_registry", 0) >= 1
    with sqlite3.connect(path) as conn:
        dump = json.dumps(conn.execute("SELECT * FROM evidence_demand").fetchall())
    assert "750ml insulated" not in dump
    assert "961700" not in dump


@pytest.mark.asyncio
async def test_verify_demand_success_partial_and_failed_field(monkeypatch, tmp_path):
    from factrail import cache as cache_mod
    from factrail.cache import SqliteCache
    path = str(tmp_path / "demand.db")
    monkeypatch.setenv("FACTRAIL_CACHE_PATH", path)
    cache_mod._global_cache = SqliteCache(db_path=path)
    monkeypatch.setattr("factrail.mcp_http_server.InseeAdapter", lambda: type("Adapter", (),
        {"lookup_with_bodacc": lambda self, _: sample_company()})())
    call = lambda fields: handle_call_tool(None, types.CallToolRequestParams(name="factrail_verify",
        arguments={"subject_type": "company_fr", "identifier": "784671695", "fields": fields}))
    assert not (await call(["status"])).is_error
    assert not (await call(["status", "naf_code"])).is_error
    assert (await call(["private_field"])).is_error
    report = DemandStore(path).report()
    assert report["total_calls"] == 3
    assert report["incomplete_coverage"].get("partial") == 1
    assert "private_field" not in json.dumps(report)


@pytest.mark.asyncio
async def test_telemetry_failure_does_not_fail_operation(monkeypatch):
    monkeypatch.setattr("factrail.evidence.demand.DemandStore", lambda: (_ for _ in ()).throw(OSError("broken")))
    result = await handle_call_tool(None, types.CallToolRequestParams(name="factrail_assess",
        arguments={"assessment_type": "unsupported", "parameters": {}}))
    assert result.is_error


def test_demand_report_aggregation_and_privacy(tmp_path):
    store = DemandStore(str(tmp_path / "db.sqlite"))
    store.record(operation="verify", capability="company_fr", requested_fields=["status"],
        outcome="supported", coverage="sufficient")
    store.record(operation="verify", capability="secret_credential", requested_fields=["private_value"],
        outcome="error", coverage="partial", unresolved_fields=["private_value"], unsupported_capability=True,
        source_failures=["government_registry"])
    report = DemandStore(str(tmp_path / "db.sqlite")).report()
    assert report["total_calls"] == 2
    assert report["requested_fields"]["status"] == 1
    assert report["incomplete_coverage"]["partial"] == 1
    assert "secret_credential" not in json.dumps(report)
    assert "private_value" not in json.dumps(report)
