"""Offline Evidence Core tests."""
from datetime import datetime, timedelta, timezone
import json
import re

import pytest
import mcp.types as types
from pydantic import ValidationError

from factrail.evidence.company_fr import resolve_company_fr
from factrail.evidence.models import (Conflict, ConflictSide, Coverage, CoverageLevel,
    EvidenceEnvelope, EvidenceSource, Fact, Freshness, Subject, VerificationStatus)
from factrail.evidence.receipts import ReceiptRepository, receipt_id_for
from factrail.models import Address, FrenchCompany, SourceMeta
from factrail.mcp_http_server import handle_call_tool, handle_list_tools

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def sample_envelope():
    return EvidenceEnvelope(
        status=VerificationStatus.SUPPORTED, subject=Subject(type="company_fr", identifiers={"siren": "784671695"}),
        facts=[Fact(field="status", value="active", evidence_ids=["insee-siren"])],
        evidence=[EvidenceSource(id="insee-siren", source_type="government_registry", authority_class="official",
            publisher="INSEE", retrieved_at=NOW, source_status="available")],
        conflicts=[], coverage=Coverage(level=CoverageLevel.SUFFICIENT, fields_requested=["status"],
            fields_resolved=["status"], fields_unresolved=[]),
        freshness=Freshness(generated_at=NOW, oldest_supporting_retrieved_at=NOW,
            newest_supporting_retrieved_at=NOW, stale=False), generated_at=NOW)


def sample_company(events=None):
    return FrenchCompany(siren="784671695", legal_name="EXAMPLE SAS", status="active",
        legal_form_code="5710", address=Address(locality="Paris"), checked_at=NOW,
        sources=[SourceMeta(id="insee-siren", name="INSEE", url="https://example.com/siren", retrieved_at=NOW),
                 SourceMeta(id="insee-siret", name="INSEE", url="https://example.com/siret", retrieved_at=NOW)],
        events=events)


def test_envelope_and_invalid_status():
    envelope = sample_envelope()
    assert envelope.facts[0].evidence_ids == ["insee-siren"]
    with pytest.raises(ValidationError):
        EvidenceEnvelope.model_validate({**envelope.model_dump(), "status": "maybe"})
    with pytest.raises(ValidationError):
        EvidenceEnvelope.model_validate({**envelope.model_dump(), "facts": [Fact(field="x", value=1, evidence_ids=["missing"]).model_dump()]})


def test_conflict_coverage_freshness_serialization():
    e = sample_envelope()
    e.evidence.append(EvidenceSource(id="other", source_type="registry", authority_class="official", publisher="Other", retrieved_at=NOW, source_status="available"))
    e.conflicts.append(Conflict(field="status", sides=[ConflictSide(value="active", evidence_ids=["insee-siren"]),
        ConflictSide(value="inactive", evidence_ids=["other"])], resolution_status="unresolved"))
    encoded = e.model_dump(mode="json")
    assert encoded["conflicts"][0]["sides"][1]["evidence_ids"] == ["other"]
    assert encoded["coverage"]["level"] == "sufficient"
    assert encoded["freshness"]["stale"] is False


def test_receipt_hash_stability_and_change():
    e = sample_envelope()
    receipt = receipt_id_for(e)
    assert re.fullmatch(r"fr_[0-9a-f]{64}", receipt)
    e.generated_at += timedelta(hours=1)
    e.freshness.generated_at += timedelta(hours=1)
    assert receipt_id_for(e) == receipt
    e.facts[0].value = "inactive"
    assert receipt_id_for(e) != receipt


def test_receipt_persistence(tmp_path):
    path = str(tmp_path / "receipts.db")
    saved = ReceiptRepository(path).save(sample_envelope())
    assert ReceiptRepository(path).get(saved.receipt_id) == saved
    assert ReceiptRepository(path).get("fr_missing") is None


def test_company_resolver_and_degraded_source():
    events = {"source_status": "unavailable", "source_error": "timeout"}
    envelope = resolve_company_fr("784671695", lookup=lambda _: sample_company(events))
    assert envelope.subject.identifiers["siren"] == "784671695"
    assert {f.field: f.value for f in envelope.facts}["status"] == "active"
    assert {f.field: f.value for f in envelope.facts}["legal_name"] == "EXAMPLE SAS"
    assert all(set(f.evidence_ids) <= {s.id for s in envelope.evidence} for f in envelope.facts)
    assert next(s for s in envelope.evidence if s.id == "bodacc").source_status == "unavailable"
    assert envelope.coverage.metadata["bodacc_source_status"] == "unavailable"


def test_historical_notice_does_not_change_status():
    events = {"source_status": "available", "historical_removal_notices": [{"publication_date": "2020-01-01"}]}
    envelope = resolve_company_fr("784671695", lookup=lambda _: sample_company(events))
    assert next(f for f in envelope.facts if f.field == "status").value == "active"
    assert next(f for f in envelope.facts if f.field == "historical_removal_notice").evidence_ids == ["bodacc"]


@pytest.mark.asyncio
async def test_mcp_tools_and_receipt(monkeypatch, tmp_path):
    from factrail import cache as cache_mod
    from factrail.cache import SqliteCache
    cache_mod._global_cache = SqliteCache(db_path=str(tmp_path / "core.db"))
    monkeypatch.setenv("FACTRAIL_CACHE_PATH", str(tmp_path / "core.db"))
    monkeypatch.setattr("factrail.mcp_http_server.InseeAdapter", lambda: type("Adapter", (), {"lookup_with_bodacc": lambda self, _: sample_company()})())
    listed = await handle_list_tools(None, None)
    names = {tool.name for tool in listed.tools}
    assert {"factrail_verify", "factrail_get_receipt", "verify_french_company", "assess_import", "analyze_company"} <= names
    call = lambda name, args: handle_call_tool(None, types.CallToolRequestParams(name=name, arguments=args))
    result = await call("factrail_verify", {"subject_type": "company_fr", "identifier": "784671695", "fields": ["status"]})
    assert not result.is_error
    payload = result.structured_content
    assert EvidenceEnvelope.model_validate(payload).facts[0].field == "status"
    assert payload["receipt_id"].startswith("fr_")
    fetched = await call("factrail_get_receipt", {"receipt_id": payload["receipt_id"]})
    assert fetched.structured_content == payload
    assert (await call("factrail_get_receipt", {"receipt_id": "fr_missing"})).is_error
    assert (await call("factrail_verify", {"subject_type": "trade", "identifier": "784671695"})).is_error
    assert (await call("factrail_verify", {"subject_type": "company_fr", "identifier": "bad"})).is_error
    old = await call("verify_french_company", {"identifier": "784671695"})
    assert json.loads(old.content[0].text)["legal_name"] == "EXAMPLE SAS"
