from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import mcp.types as types
import pytest

from factrail.company import AnalyzeCompanyInput, analyze_company
from factrail.evidence.company_fr import resolve_company_fr
from factrail.evidence.models import SupportLevel
from factrail.evidence.receipts import ReceiptIntegrityError, ReceiptRepository, receipt_id_for
from factrail.evidence.service import capability_registry
from factrail.mcp_http_server import handle_call_tool, handle_list_tools
from tests.test_evidence import sample_company, sample_envelope
from tests.test_evidence_v21 import import_input

SCHEMA = json.loads(Path("schemas/evidence-envelope-1.2.schema.json").read_text())


def test_analyze_company_returns_only_explicit_unavailable_values():
    result = analyze_company(AnalyzeCompanyInput(siren="784671695", company_name="Caller name"))
    payload = result.model_dump(mode="json")
    assert payload["status"] == "insufficient_evidence"
    assert payload["company_name"] == "Caller name"
    assert payload["financial_health"]["score"] is None
    assert payload["credit_risk"]["score"] is None
    assert payload["credit_risk"]["rating"] is None
    assert payload["business_risk"]["financial"] is None
    assert payload["executive_team"]["size"] is None
    assert payload["confidence"] is None
    assert payload["unavailable"]["credit_score"]["reason"] == "source_not_integrated"


def test_schema_matches_model_and_validates_company_envelope():
    from factrail.evidence.models import EvidenceEnvelope
    generated = EvidenceEnvelope.model_json_schema()
    generated.update({"$schema": SCHEMA["$schema"], "$id": SCHEMA["$id"]})
    assert generated == SCHEMA
    envelope = resolve_company_fr("784671695", lookup=lambda _: sample_company())
    jsonschema.validate(envelope.model_dump(mode="json"), SCHEMA)
    assert all(f.support_level == SupportLevel.AUTHORITATIVE for f in envelope.facts)
    malformed = envelope.model_dump(mode="json")
    malformed["status"] = "bogus"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(malformed, SCHEMA)


def test_trade_envelope_schema_and_provisional_lineage(monkeypatch):
    from factrail.evidence.trade import assess_import_evidence
    class EmptyTaric:
        def get_measures(self, *args, **kwargs):
            return {"measures": [], "_source": "live", "source_detail": "parser_no_usable_measures"}
    monkeypatch.setattr("factrail.trade.service.get_default_store", lambda: EmptyTaric())
    envelope = assess_import_evidence(import_input())
    jsonschema.validate(envelope.model_dump(mode="json"), SCHEMA)
    facts = {fact.field: fact for fact in envelope.facts}
    assert facts["goods_value"].support_level == SupportLevel.CALLER_INPUT
    if "estimated_total" in facts:
        assert facts["estimated_total"].support_level == SupportLevel.DERIVED_PROVISIONAL
        assert facts["estimated_total"].metadata["derivation"]["method"] == "factrail_landed_cost_v1"
        assert facts["estimated_total"].metadata["derivation"]["provisional_inputs"]
    assert envelope.coverage.level.value == "partial"
    assert any(source.metadata.get("source_detail") == "parser_no_usable_measures" for source in envelope.evidence)


def test_a2m_no_measures_is_explicit_and_empty_parse_is_not_confirmation():
    from factrail.trade.taric_store import _explicit_no_measures
    assert _explicit_no_measures("<div>No applicable measures</div>")
    assert not _explicit_no_measures("<div>Search results</div>")


def test_taric_parser_error_and_stale_cache_are_distinct(tmp_path, monkeypatch):
    from factrail.trade.taric_store import TaricParserError, TaricStore, TaricUnavailable
    store = TaricStore(str(tmp_path / "taric.db"))
    monkeypatch.setattr(store, "lookup", lambda *_: None)
    monkeypatch.setattr(store, "fetch_measures", lambda *_: (_ for _ in ()).throw(TaricParserError("safe parser marker")))
    with pytest.raises(TaricUnavailable) as error:
        store.get_measures("961700", "CN", "FR")
    assert error.value.source_detail == "parser_error"

    stale = {"cached": True, "stale": True, "measures": [], "source_detail": "response_complete"}
    calls = iter((stale.copy(),))
    monkeypatch.setattr(store, "lookup", lambda *_: next(calls))
    assert store.get_measures("961700", "CN", "FR")["source_detail"] == "stale_cache_after_refresh_failure"


def test_stale_tariff_cache_marks_envelope_stale_and_partial(monkeypatch):
    from factrail.evidence.trade import assess_import_evidence
    class StaleTaric:
        def get_measures(self, *args, **kwargs):
            return {"measures": [{"measure_kind": "third_country_duty", "tariff": "3%", "origin_area": "ERGA OMNES"}],
                    "version": "old", "version_date": "2024-01-01", "retrieved_at": "2024-01-01T00:00:00+00:00",
                    "_warning": "refresh failed", "source_detail": "stale_cache_after_refresh_failure"}
    monkeypatch.setattr("factrail.trade.service.get_default_store", lambda: StaleTaric())
    envelope = assess_import_evidence(import_input())
    assert envelope.freshness.stale is True
    assert envelope.coverage.metadata["source_details"]["tariff_registry"] == "stale_cache_after_refresh_failure"
    assert envelope.coverage.level.value == "partial"


def test_receipt_integrity_detects_tampering(tmp_path):
    repo = ReceiptRepository(str(tmp_path / "receipt.db"))
    saved = repo.save(sample_envelope())
    assert repo.get(saved.receipt_id) == saved
    with sqlite3.connect(repo.db_path) as db:
        row = db.execute("select envelope from evidence_receipts where receipt_id=?", (saved.receipt_id,)).fetchone()
        data = json.loads(row[0])
        data["facts"][0]["value"] = "tampered"
        db.execute("update evidence_receipts set envelope=? where receipt_id=?", (json.dumps(data), saved.receipt_id))
    with pytest.raises(ReceiptIntegrityError):
        repo.get(saved.receipt_id)
    tampered_envelope = sample_envelope()
    tampered_envelope.facts[0].value = "tampered"
    assert receipt_id_for(tampered_envelope) != saved.receipt_id


@pytest.mark.parametrize("schema_version", ["1.0", "1.1"])
def test_historical_receipt_schema_hashes_remain_supported(tmp_path, schema_version):
    envelope = sample_envelope().model_copy(update={"schema_version": schema_version})
    repo = ReceiptRepository(str(tmp_path / f"receipt-{schema_version}.db"))
    saved = repo.save(envelope)
    assert repo.get(saved.receipt_id).schema_version == schema_version


@pytest.mark.asyncio
async def test_capability_registry_and_mcp_filtering(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTRAIL_CACHE_PATH", str(tmp_path / "events.db"))
    registry = capability_registry()
    assert {item["capability"] for item in registry} == {"company_fr", "import"}
    assert next(x for x in registry if x["capability"] == "import")["required_inputs"]
    listed = {tool.name for tool in (await handle_list_tools(None, None)).tools}
    assert "factrail_capabilities" in listed
    result = await handle_call_tool(None, types.CallToolRequestParams(name="factrail_capabilities", arguments={"operation": "verify"}))
    assert result.structured_content["capabilities"][0]["capability"] == "company_fr"


@pytest.mark.asyncio
async def test_capability_mcp_and_analyze_legacy_callable(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTRAIL_CACHE_PATH", str(tmp_path / "db.sqlite"))
    result = await handle_call_tool(None, types.CallToolRequestParams(name="analyze_company", arguments={"company_name": "Caller"}))
    assert not result.is_error
    payload = result.structured_content
    assert payload["credit_risk"]["score"] is None
    assert payload["status"] == "insufficient_evidence"


@pytest.mark.asyncio
async def test_demand_marks_unsupported_field_separately(tmp_path, monkeypatch):
    from factrail.evidence.demand import DemandStore
    monkeypatch.setenv("FACTRAIL_CACHE_PATH", str(tmp_path / "demand.sqlite"))
    await handle_call_tool(None, types.CallToolRequestParams(name="factrail_verify", arguments={
        "subject_type": "company_fr", "identifier": "784671695", "fields": ["not_a_field"]}))
    report = DemandStore().report(days=1, all_origins=True)
    assert report["request_support_status"] == {"unsupported_field": 1}
    assert report["capability_registry_versions"] == {"2.3": 1}
