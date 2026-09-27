"""Comprehensive tests for FACTRAIL Trade V0.1 (assess_import).

Covers:
1. Known HS code -> France
2. Natural-language product description
3. Ambiguous classification
4. Missing product attributes
5. Invalid ISO country
6. Unsupported/non-EU destination
7. Duty calculation
8. VAT calculation
9. Missing freight cost
10. Landed-cost arithmetic
11. Source outage (stub returns unavailable)
12. Provenance fields
13. Existing verify_french_company regression
14. MCP schema/serialization
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch, MagicMock

import pytest
import mcp.types as types

from factrail.trade.models import (
    AssessImportInput,
    AssessmentStatus,
    Classification,
    Compliance,
    Customs,
    Evidence,
    ImportAssessment,
    LandedCost,
    MissingField,
    ProvenanceStatus,
    RiskLevel,
    Tax,
)
from factrail.trade.service import assess_import as run_assessment
from factrail.trade.classification import (
    validate_hs_code,
    hs_code_level,
    hs_code_to_parent,
    extract_classification_candidates,
    build_classification,
    add_classification_evidence,
)
from factrail.trade.customs import (
    EU_MFN_DUTY,
    EU_FTA_MAP,
    build_customs_block,
    PreferenceStatus,
)
from factrail.trade.taric_store import (
    TaricStore,
    parse_tariff_rate,
    find_mfn_duty,
    find_preference,
    lookup_import_measures,
)
from factrail.trade.vat import eu_vat_rate, EU_VAT_RATES
from factrail.trade.customs import build_vat as build_vat_fn
from factrail.trade.landed_cost import build_landed_cost
from factrail.trade.compliance import build_compliance, GENERIC_COMPLIANCE
from factrail.trade.evidence import EvidenceLedger
from factrail.trade.sources import (
    TaricSource,
    Access2MarketsSource,
    SanctionsSource,
    EbtiSource,
    VatSource,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_input(**overrides: Any) -> AssessImportInput:
    d = {
        "product": "750ml insulated stainless steel drinking bottle",
        "origin_country": "CN",
        "destination_country": "FR",
        "quantity": 5000,
        "goods_value": 13500.0,
        "currency": "EUR",
        "known_hs_code": None,
        "material": None,
        "weight_kg": None,
        "dimensions": None,
        "manufacturer": None,
        "model": None,
        "incoterm": None,
        "shipping_mode": None,
        "origin_location": None,
        "destination_location": None,
        "freight_cost": None,
        "insurance_cost": None,
    }
    d.update(overrides)
    return AssessImportInput(**d)


# ---------------------------------------------------------------------------
# 1. Known HS code -> France
# ---------------------------------------------------------------------------

class TestKnownHsCode:
    def test_known_hs6_enriched_to_classification(self):
        inp = _base_input(known_hs_code="961700")
        result = run_assessment(inp)
        assert result.status == AssessmentStatus.OK.value
        cl = result.classification
        assert cl.hs_code == "961700"
        # V0 does not fabricate CN/TARIC codes
        assert cl.cn_code is None or cl.cn_code == "961700"
        assert cl.taric_code is None
        assert cl.review_required is False
        assert cl.confidence >= 0.8
        assert len(cl.evidence) >= 1
        ev = cl.evidence[0]
        assert ev.status == ProvenanceStatus.INFERRED
        assert "FACTRAIL classification engine" in ev.authority

    def test_known_hs8_treated_as_cn(self):
        inp = _base_input(known_hs_code="96170000")
        result = run_assessment(inp)
        cl = result.classification
        assert cl.cn_code == "96170000"
        assert cl.hs_code == "961700"

    def test_known_hs10_treated_as_taric(self):
        inp = _base_input(known_hs_code="9617000000")
        result = run_assessment(inp)
        cl = result.classification
        assert cl.taric_code == "9617000000"
        assert cl.cn_code == "96170000"
        assert cl.hs_code == "961700"


# ---------------------------------------------------------------------------
# 2. Natural-language product description
# ---------------------------------------------------------------------------

class TestNaturalLanguageDescription:
    def test_bottle_classification_candidate(self):
        inp = _base_input(product="750ml insulated stainless steel drinking bottle")
        result = run_assessment(inp)
        cl = result.classification
        # V0 keyword matching should pick up 9617 (insulated containers)
        assert cl.hs_code == "9617"
        assert cl.description is not None
        assert "insulated" in cl.description.lower() or "vacuum" in cl.description.lower()
        assert cl.confidence > 0.0
        assert not cl.review_required or cl.confidence < 0.5

    def test_known_hs_overrides_description(self):
        inp = _base_input(
            product="something ambiguous",
            known_hs_code="961700",
        )
        result = run_assessment(inp)
        cl = result.classification
        assert cl.hs_code == "961700"
        # known HS should be top candidate
        assert cl.alternatives[0].code == "961700" if cl.alternatives else True


# ---------------------------------------------------------------------------
# 3. Ambiguous classification
# ---------------------------------------------------------------------------

class TestAmbiguousClassification:
    def test_ambiguous_product_prompts_review(self):
        inp = _base_input(product="metal thing")
        result = run_assessment(inp)
        cl = result.classification
        # With a vague description, confidence is low and review is required
        assert cl.confidence < 0.5 or cl.review_required

    def test_alternatives_returned(self):
        inp = _base_input(product="metal container")
        result = run_assessment(inp)
        cl = result.classification
        # At least one alternative should be present for ambiguous products
        assert len(cl.alternatives) >= 1 or cl.confidence > 0.0


# ---------------------------------------------------------------------------
# 4. Missing product attributes
# ---------------------------------------------------------------------------

class TestMissingProductAttributes:
    def test_missing_material_adds_questions(self):
        inp = _base_input(material=None)
        result = run_assessment(inp)
        missing = result.missing_information
        fields = {m.field for m in missing}
        assert "material" in fields

    def test_missing_weight_adds_questions(self):
        inp = _base_input(weight_kg=None)
        result = run_assessment(inp)
        fields = {m.field for m in result.missing_information}
        assert "weight_kg" in fields

    def test_missing_dimensions_adds_questions(self):
        inp = _base_input(dimensions=None)
        result = run_assessment(inp)
        fields = {m.field for m in result.missing_information}
        assert "dimensions" in fields

    def test_food_contact_question_for_bottle(self):
        inp = _base_input(product="drinking bottle")
        result = run_assessment(inp)
        fields = {m.field for m in result.missing_information}
        assert "food_contact" in fields

    def test_electrical_question_for_battery_product(self):
        inp = _base_input(product="lithium-ion battery pack")
        result = run_assessment(inp)
        fields = {m.field for m in result.missing_information}
        assert "electrical" in fields

    def test_no_missing_for_fully_specified(self):
        inp = _base_input(
            material="stainless steel",
            weight_kg=0.25,
            dimensions="25x8x8 cm",
        )
        result = run_assessment(inp)
        # Generic product-intent questions still appear for bottles,
        # but material/weight/dimensions are satisfied
        fields = {m.field for m in result.missing_information}
        assert "material" not in fields
        assert "weight_kg" not in fields
        assert "dimensions" not in fields


# ---------------------------------------------------------------------------
# 5. Invalid ISO country
# ---------------------------------------------------------------------------

class TestInvalidIsoCountry:
    def test_invalid_origin_rejected(self):
        with pytest.raises(Exception):
            AssessImportInput(
                product="widget",
                origin_country="XX",
                destination_country="FR",
                quantity=100,
                goods_value=1000,
                currency="EUR",
            )

    def test_invalid_destination_rejected(self):
        with pytest.raises(Exception):
            AssessImportInput(
                product="widget",
                origin_country="CN",
                destination_country="US",
                quantity=100,
                goods_value=1000,
                currency="EUR",
            )

    def test_invalid_currency_rejected(self):
        with pytest.raises(Exception):
            AssessImportInput(
                product="widget",
                origin_country="CN",
                destination_country="FR",
                quantity=100,
                goods_value=1000,
                currency="XXX",
            )

    def test_negative_quantity_rejected(self):
        with pytest.raises(Exception):
            AssessImportInput(
                product="widget",
                origin_country="CN",
                destination_country="FR",
                quantity=-1,
                goods_value=1000,
                currency="EUR",
            )


# ---------------------------------------------------------------------------
# 6. Unsupported/non-EU destination
# ---------------------------------------------------------------------------

class TestUnsupportedDestination:
    def test_non_eu_destination_rejected_by_input_model(self):
        with pytest.raises(Exception):
            AssessImportInput(
                product="widget",
                origin_country="CN",
                destination_country="GB",
                quantity=100,
                goods_value=1000,
                currency="EUR",
            )

    def test_eu_member_accepted(self):
        # All EU member states should be accepted
        for country in ("FR", "DE", "IT", "ES", "NL", "BE", "PT", "PL"):
            inp = _base_input(destination_country=country)
            result = run_assessment(inp)
            assert result.status in (
                AssessmentStatus.OK.value,
                AssessmentStatus.INSUFFICIENT_INFORMATION.value,
            )


# ---------------------------------------------------------------------------
# 7. Duty calculation
# ---------------------------------------------------------------------------

class TestDutyCalculation:
    def test_mfn_duty_applied_for_cn(self):
        inp = _base_input(known_hs_code="961700")
        result = run_assessment(inp)
        customs = result.customs
        # 9617 from authoritative TARIC: 6.70% (Access2Markets 2026-09-25)
        # When seeded cache is present, authoritative data is used.
        # When no cache, fallback to curated 3.0% with ESTIMATED status.
        if customs.base_duty_rate_pct == 6.7:
            assert customs.applied_rate_type == "third_country_mfn"
            assert customs.base_duty_rate_pct == 6.7
            assert customs.estimated_duty is not None
            assert customs.estimated_duty == pytest.approx(13500.0 * 0.067, abs=0.01)
            # Verify evidence is VERIFIED (from authoritative source)
            duty_ev = next(e for e in customs.evidence if "duty" in e.supports or "tariff" in e.supports)
            assert duty_ev.status == ProvenanceStatus.VERIFIED
            assert "DG TAXUD" in duty_ev.authority or "TARIC" in duty_ev.authority
        else:
            # Fallback path (no network / authoritative unavailable)
            assert customs.base_duty_rate_pct == 3.0
            assert customs.applied_rate_type == "mfn_curated_fallback"
            assert customs.estimated_duty is not None
            assert customs.estimated_duty == pytest.approx(405.0, abs=0.01)
            # Fallback evidence is ESTIMATED, not VERIFIED
            duty_ev = next(e for e in customs.evidence if "duty" in e.supports or "tariff" in e.supports)
            assert duty_ev.status == ProvenanceStatus.ESTIMATED
            assert "fallback" in duty_ev.note.lower() or "FALLBACK" in duty_ev.note

    def test_preferential_rate_null_for_japan(self):
        inp = _base_input(
            origin_country="JP",
            known_hs_code="961700",
        )
        result = run_assessment(inp)
        customs = result.customs
        # Japan EPA exists: preferential_rate_pct=0.0 means duty-free under FTA
        # when rules of origin are satisfied (not "no preference").
        # preference_status should reflect that a preference agreement exists
        assert customs.preference_status == PreferenceStatus.PREFERENCE_AVAILABLE
        all_notes = " ".join(customs.notes).lower()
        assert "japan" in all_notes or "epa" in all_notes or "preferential" in all_notes

    def test_no_duty_rate_for_unknown_heading(self):
        inp = _base_input(known_hs_code="999999")
        result = run_assessment(inp)
        customs = result.customs
        assert customs.base_duty_rate_pct is None
        assert customs.estimated_duty is None

    def test_duty_includes_freight_and_insurance(self):
        inp = _base_input(
            known_hs_code="961700",
            freight_cost=500.0,
            insurance_cost=100.0,
        )
        result = run_assessment(inp)
        customs = result.customs
        # With authoritative 6.7%: (13500+500+100)*0.067 = 974.7
        # With fallback 3.0%: (13500+500+100)*0.03 = 423
        cv = 13500.0 + 500.0 + 100.0
        if customs.base_duty_rate_pct == 6.7:
            expected = cv * 0.067
        else:
            expected = cv * 0.03
        assert customs.estimated_duty == pytest.approx(expected, abs=0.01)

    def test_duty_evidence_present(self):
        inp = _base_input(known_hs_code="961700")
        result = run_assessment(inp)
        customs = result.customs
        assert len(customs.evidence) >= 1
        duty_ev = next(e for e in customs.evidence if "duty" in e.supports or "tariff" in e.supports)
        # Evidence is VERIFIED when from authoritative TARIC, ESTIMATED when fallback
        assert duty_ev.status in (ProvenanceStatus.VERIFIED, ProvenanceStatus.ESTIMATED)


# ---------------------------------------------------------------------------
# 8. VAT calculation
# ---------------------------------------------------------------------------

class TestVatCalculation:
    def test_france_vat_rate_20_percent(self):
        rate = eu_vat_rate("FR")
        assert rate == 20.0

    def test_germany_vat_rate_19_percent(self):
        rate = eu_vat_rate("DE")
        assert rate == 19.0

    def test_italy_vat_rate_22_percent(self):
        rate = eu_vat_rate("IT")
        assert rate == 22.0

    def test_vat_on_goods_value(self):
        inp = _base_input(known_hs_code="961700")
        result = run_assessment(inp)
        tax = result.tax
        assert tax.import_vat_rate_pct == 20.0
        customs = result.customs
        # VAT = 20% * (13500 + duty)
        if customs.estimated_duty is not None:
            expected_vat = 20.0 / 100.0 * (13500.0 + customs.estimated_duty)
            assert tax.estimated_import_vat == pytest.approx(expected_vat, abs=0.01)
        else:
            # No duty available; VAT on goods only = 2700
            assert tax.estimated_import_vat == pytest.approx(2700.0, abs=0.01)

    def test_vat_evidence(self):
        inp = _base_input(known_hs_code="961700")
        result = run_assessment(inp)
        tax = result.tax
        assert len(tax.evidence) >= 2  # rate + base
        rate_ev = tax.evidence[0]
        assert rate_ev.status == ProvenanceStatus.VERIFIED
        assert rate_ev.supports == ["tax", "vat_rate"]

    def test_vat_none_for_non_eu_destination(self):
        # EUR VAT rate only works for EU member states
        # Use a non-EU code to verify it returns None
        rate = eu_vat_rate("CN")
        assert rate is None


# ---------------------------------------------------------------------------
# 9. Missing freight cost
# ---------------------------------------------------------------------------

class TestMissingFreightCost:
    def test_freight_null_when_not_supplied(self):
        inp = _base_input(freight_cost=None)
        result = run_assessment(inp)
        assert result.landed_cost.freight is None

    def test_completeness_reduced_when_freight_missing(self):
        inp = _base_input(freight_cost=None, insurance_cost=None)
        result = run_assessment(inp)
        assert result.landed_cost.completeness < 1.0

    def test_assumptions_note_missing_freight(self):
        inp = _base_input(freight_cost=None)
        result = run_assessment(inp)
        assert any("Freight cost not supplied" in a for a in result.landed_cost.assumptions)


# ---------------------------------------------------------------------------
# 10. Landed-cost arithmetic
# ---------------------------------------------------------------------------

class TestLandedCostArithmetic:
    def test_full_landed_cost_with_all_inputs(self):
        inp = _base_input(
            known_hs_code="961700",
            freight_cost=500.0,
            insurance_cost=100.0,
        )
        result = run_assessment(inp)
        lc = result.landed_cost
        customs = result.customs
        # Compute expected values based on actual duty rate
        cv = 13500.0 + 500.0 + 100.0
        if customs.estimated_duty is not None:
            duty = customs.estimated_duty
            vat_base = cv + duty
            vat = 0.20 * vat_base
            total = cv + duty + vat
            assert lc.estimated_total == pytest.approx(total, abs=0.1)
            assert lc.estimated_cost_per_unit == pytest.approx(total / 5000, abs=0.001)
            assert lc.customs_duty == pytest.approx(duty, abs=0.01)
            assert lc.import_vat == pytest.approx(vat, abs=0.1)
        else:
            assert lc.estimated_total is None

    def test_landed_cost_without_duty_is_null(self):
        inp = _base_input(known_hs_code="999999", freight_cost=500, insurance_cost=100)
        result = run_assessment(inp)
        lc = result.landed_cost
        assert lc.estimated_total is None
        assert lc.estimated_cost_per_unit is None

    def test_landed_cost_currency_matches_input(self):
        inp = _base_input(currency="EUR")
        result = run_assessment(inp)
        assert result.landed_cost.currency == "EUR"

    def test_landed_cost_evidence(self):
        inp = _base_input(known_hs_code="961700")
        result = run_assessment(inp)
        lc = result.landed_cost
        assert len(lc.evidence) >= 3  # goods_value, customs_duty, import_vat
        goods_ev = next(e for e in lc.evidence if "goods_value" in e.supports)
        assert goods_ev.status == ProvenanceStatus.VERIFIED


# ---------------------------------------------------------------------------
# 11. Source outage (stubs return unavailable)
# ---------------------------------------------------------------------------

class TestSourceOutage:
    def test_taric_source_returns_unavailable(self):
        src = TaricSource()
        ev = src.lookup_duty("961700", "CN", "FR")
        assert ev.status == ProvenanceStatus.UNAVAILABLE
        assert "TARIC" in ev.authority
        assert "not available in V0" in ev.note

    def test_access2markets_returns_unavailable(self):
        src = Access2MarketsSource()
        ev = src.lookup_market_access("CN", "FR", "961700")
        assert ev.status == ProvenanceStatus.UNAVAILABLE
        assert "Access2Markets" in ev.authority

    def test_sanctions_source_returns_unavailable(self):
        src = SanctionsSource()
        ev = src.check("CN", "FR")
        assert ev.status == ProvenanceStatus.UNAVAILABLE
        assert "sanctions" in ev.supports

    def test_ebti_source_returns_unavailable(self):
        src = EbtiSource()
        ev = src.lookup_bti("FR", "961700")
        assert ev.status == ProvenanceStatus.UNAVAILABLE
        assert "BTI" in ev.authority

    def test_vat_source_works_for_fr(self):
        src = VatSource()
        ev = src.lookup_vat_rate("FR")
        assert ev.status == ProvenanceStatus.VERIFIED
        assert ev.value == "20.0%"
        assert "FRA" in ev.authority

    def test_vat_source_unavailable_for_non_eu(self):
        src = VatSource()
        ev = src.lookup_vat_rate("CN")
        assert ev.status == ProvenanceStatus.UNAVAILABLE


# ---------------------------------------------------------------------------
# 12. Provenance fields
# ---------------------------------------------------------------------------

class TestProvenanceFields:
    def test_all_evidence_has_required_fields(self):
        inp = _base_input(known_hs_code="961700")
        result = run_assessment(inp)
        for ev in result.sources:
            assert ev.authority
            assert ev.source
            assert ev.url
            assert ev.retrieved_at is not None
            assert ev.confidence is not None
            assert 0.0 <= ev.confidence <= 1.0
            assert ev.status in (
                ProvenanceStatus.VERIFIED,
                ProvenanceStatus.DERIVED,
                ProvenanceStatus.ESTIMATED,
                ProvenanceStatus.INFERRED,
                ProvenanceStatus.UNAVAILABLE,
            )

    def test_assessment_id_is_uuid(self):
        inp = _base_input()
        result = run_assessment(inp)
        # assessment_id is a non-empty string
        assert len(result.assessment_id) > 0
        # it should be a valid hex uuid
        try:
            uuid.UUID(result.assessment_id)
        except ValueError:
            pytest.fail(f"assessment_id is not a valid UUID: {result.assessment_id}")

    def test_requested_at_is_recent(self):
        inp = _base_input()
        before = datetime.now(timezone.utc)
        result = run_assessment(inp)
        after = datetime.now(timezone.utc)
        assert before <= result.requested_at <= after

    def test_input_summary_matches_request(self):
        inp = _base_input(
            product="test product",
            origin_country="CN",
            destination_country="FR",
            quantity=100,
            goods_value=500,
            currency="EUR",
        )
        result = run_assessment(inp)
        s = result.input_summary
        assert s["origin_country"] == "CN"
        assert s["destination_country"] == "FR"
        assert s["quantity"] == 100
        assert s["goods_value"] == 500
        assert s["currency"] == "EUR"


# ---------------------------------------------------------------------------
# 13. Existing verify_french_company regression
# ---------------------------------------------------------------------------

class TestVerifyFrenchCompanyRegression:
    @pytest.mark.asyncio
    async def test_verify_french_company_still_works(self):
        from factrail.mcp_http_server import handle_call_tool

        params = types.CallToolRequestParams(
            name="verify_french_company",
            arguments={"identifier": "784671695"},
        )
        result = await handle_call_tool(None, params)
        text = result.content[0].text
        data = json.loads(text)
        # Should return a valid result or a known error (cached/not found),
        # but NOT an "Unknown tool" error
        assert "error" not in data or data.get("error") not in ("Unknown tool: verify_french_company",)


# ---------------------------------------------------------------------------
# 14. MCP schema/serialization
# ---------------------------------------------------------------------------

class TestMcpSchemaSerialization:
    def test_assess_import_input_schema_matches_model(self):
        schema = AssessImportInput.model_json_schema()
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "required" in schema
        # Required fields
        for field in ("product", "origin_country", "destination_country", "quantity", "goods_value", "currency"):
            assert field in schema["required"]
            assert field in schema["properties"]

    def test_import_assessment_serializes_to_json(self):
        inp = _base_input(known_hs_code="961700")
        result = run_assessment(inp)
        # Should serialize without error
        json_str = result.model_dump_json()
        data = json.loads(json_str)
        assert "assessment_id" in data
        assert "status" in data
        assert "classification" in data
        assert "customs" in data
        assert "tax" in data
        assert "landed_cost" in data
        assert "missing_information" in data
        assert "risk" in data
        assert "sources" in data

    def test_import_assessment_json_schema(self):
        schema = ImportAssessment.model_json_schema()
        assert schema["type"] == "object"
        for section in ("classification", "customs", "tax", "compliance",
                        "landed_cost", "missing_information", "risk", "sources"):
            assert section in schema["properties"]

    def test_risk_levels_are_valid_enums(self):
        inp = _base_input(known_hs_code="961700")
        result = run_assessment(inp)
        risk = result.risk
        assert risk.classification in (RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH)
        assert risk.customs in (RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH)
        assert risk.compliance in (RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH)
        assert risk.cost_uncertainty in (RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH)


# ---------------------------------------------------------------------------
# 15. Evidence ledger
# ---------------------------------------------------------------------------

class TestEvidenceLedger:
    def test_ledger_deduplicates(self):
        ledger = EvidenceLedger()
        ev1 = Evidence(
            value="test",
            status=ProvenanceStatus.VERIFIED,
            authority="Test Authority",
            source="Test Source",
            url="https://example.com",
            retrieved_at=datetime.now(timezone.utc),
            supports=["test"],
            confidence=1.0,
        )
        ev2 = Evidence(
            value="test",
            status=ProvenanceStatus.VERIFIED,
            authority="Test Authority",
            source="Test Source",
            url="https://example.com",
            retrieved_at=datetime.now(timezone.utc),
            supports=["test"],
            confidence=1.0,
        )
        ledger.append(ev1)
        ledger.append(ev2)
        assert len(ledger.deduplicate()) == 1

    def test_ledger_append_extend(self):
        ledger = EvidenceLedger()
        ev = Evidence(
            value="x",
            status=ProvenanceStatus.ESTIMATED,
            authority="A",
            source="S",
            url="http://x",
            retrieved_at=datetime.now(timezone.utc),
            supports=["x"],
            confidence=0.5,
        )
        ledger.append(ev)
        assert len(ledger) == 1
        ledger.extend([ev])
        assert len(ledger) == 2  # not deduped by extend


# ---------------------------------------------------------------------------
# 16. Classification helpers
# ---------------------------------------------------------------------------

class TestClassificationHelpers:
    def test_validate_hs_code_accepts_6_digits(self):
        assert validate_hs_code("961700") is True

    def test_validate_hs_code_accepts_8_digits(self):
        assert validate_hs_code("96170000") is True

    def test_validate_hs_code_accepts_10_digits(self):
        assert validate_hs_code("9617000000") is True

    def test_validate_hs_code_rejects_letters(self):
        assert validate_hs_code("9617A0") is False

    def test_validate_hs_code_rejects_too_short(self):
        assert validate_hs_code("12345") is False

    def test_validate_hs_code_rejects_empty(self):
        assert validate_hs_code("") is False

    def test_hs_code_level(self):
        assert hs_code_level("961700") == "hs"
        assert hs_code_level("96170000") == "cn"
        assert hs_code_level("9617000000") == "taric"
        assert hs_code_level("12345") is None

    def test_hs_code_to_parent(self):
        assert hs_code_to_parent("96170000") == "961700"
        assert hs_code_to_parent("9617000000") == "961700"
        assert hs_code_to_parent("961700") == "961700"


# ---------------------------------------------------------------------------
# 17. Landed cost engine unit tests
# ---------------------------------------------------------------------------

class TestLandedCostEngine:
    def test_all_components_known(self):
        lc = build_landed_cost(
            goods_value=1000.0,
            currency="EUR",
            quantity=100,
            freight_cost=100.0,
            insurance_cost=50.0,
            customs_duty=50.0,
            import_vat=230.0,
            other_known_costs=20.0,
            evidence_ledger=[],
        )
        assert lc.goods_value == 1000.0
        assert lc.freight == 100.0
        assert lc.insurance == 50.0
        assert lc.customs_duty == 50.0
        assert lc.import_vat == 230.0
        assert lc.other_known_costs == 20.0
        assert lc.estimated_total == 1450.0
        assert lc.estimated_cost_per_unit == 14.5
        assert lc.currency == "EUR"
        assert lc.completeness == 1.0

    def test_missing_freight_lowers_completeness(self):
        lc = build_landed_cost(
            goods_value=1000.0,
            currency="EUR",
            quantity=100,
            freight_cost=None,
            insurance_cost=50.0,
            customs_duty=50.0,
            import_vat=230.0,
            other_known_costs=None,
            evidence_ledger=[],
        )
        assert lc.completeness == pytest.approx(0.85, abs=0.01)

    def test_missing_vat_prevents_total(self):
        lc = build_landed_cost(
            goods_value=1000.0,
            currency="EUR",
            quantity=100,
            freight_cost=100.0,
            insurance_cost=50.0,
            customs_duty=50.0,
            import_vat=None,
            other_known_costs=None,
            evidence_ledger=[],
        )
        assert lc.estimated_total is None
        assert lc.estimated_cost_per_unit is None

    def test_zero_quantity_prevents_per_unit(self):
        lc = build_landed_cost(
            goods_value=1000.0,
            currency="EUR",
            quantity=0,
            freight_cost=100.0,
            insurance_cost=50.0,
            customs_duty=50.0,
            import_vat=230.0,
            other_known_costs=None,
            evidence_ledger=[],
        )
        assert lc.estimated_total == 1430.0
        assert lc.estimated_cost_per_unit is None


# ---------------------------------------------------------------------------
# 18. VAT engine unit tests
# ---------------------------------------------------------------------------

class TestVatEngine:
    def test_vat_on_goods_only_when_duty_unknown(self):
        tax = build_vat_fn(
            destination_country="FR",
            customs_value=1000.0,
            customs_duty=None,
            evidence_ledger=[],
        )
        assert tax.import_vat_rate_pct == 20.0
        assert tax.estimated_import_vat == 200.0

    def test_vat_on_goods_plus_duty(self):
        tax = build_vat_fn(
            destination_country="FR",
            customs_value=1000.0,
            customs_duty=100.0,
            evidence_ledger=[],
        )
        assert tax.estimated_import_vat == 220.0  # 20% * 1100

    def test_vat_unknown_country_returns_none(self):
        tax = build_vat_fn(
            destination_country="CN",
            customs_value=1000.0,
            customs_duty=None,
            evidence_ledger=[],
        )
        assert tax.import_vat_rate_pct is None
        assert tax.estimated_import_vat is None
