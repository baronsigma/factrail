"""Adapt the existing import engine without changing its calculations."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from factrail.trade.models import AssessImportInput, ImportAssessment, ProvenanceStatus
from factrail.trade.service import assess_import

from .models import Coverage, CoverageLevel, EvidenceEnvelope, EvidenceSource, Fact, Freshness, Subject, VerificationStatus, SupportLevel


def import_to_envelope(parameters: AssessImportInput, result: ImportAssessment) -> EvidenceEnvelope:
    now = datetime.now(timezone.utc)
    sources: list[EvidenceSource] = []
    by_support: dict[str, list[str]] = {}
    for item in result.sources:
        key = json.dumps({"value": item.value, "status": item.status.value, "authority": item.authority,
                          "source": item.source, "url": item.url, "supports": sorted(item.supports), "source_outcome": item.source_outcome},
                         sort_keys=True, default=str)
        source_id = "trade_" + hashlib.sha256(key.encode()).hexdigest()[:24]
        if source_id in {s.id for s in sources}:
            continue
        internal = item.status in (ProvenanceStatus.DERIVED, ProvenanceStatus.INFERRED)
        source_outcome = item.source_outcome
        identity = f"{item.source} {item.authority}".lower()
        if "access2markets" in identity:
            source_type = "access2markets_secondary"
            authority_class = "secondary"
        elif source_outcome and ("eu_taric" in identity or "official taric snapshot" in identity):
            source_type = "official_customs_source"
            authority_class = "official_derived_dataset"
        elif source_outcome:
            source_type = "secondary_or_unclassified_source"
            authority_class = "secondary_source"
        elif item.status == ProvenanceStatus.ESTIMATED and "CURATED" in item.source.upper():
            source_type = "curated_factrail_reference"
            authority_class = "curated_reference"
        elif item.status == ProvenanceStatus.VERIFIED and "VAT reference data" not in item.source:
            source_type = "official_public_source"
            authority_class = "official_government_customs_source"
        elif internal:
            source_type = "internal_engine"
            authority_class = "deterministic_calculation"
        else:
            source_type = "curated_factrail_reference"
            authority_class = "curated_reference"
        sources.append(EvidenceSource(
            id=source_id, source_type=source_type, authority_class=authority_class,
            publisher=item.source, url=None if internal else (item.url or None), retrieved_at=item.retrieved_at,
            source_status=item.status.value,
            metadata={"authority": item.authority, "effective_date_text": item.effective_date,
                      "note": item.note, "assertion_value": item.value, "supports": item.supports,
                      "source_outcome": source_outcome, "source_detail": item.source_detail},
        ))
        for support in item.supports:
            by_support.setdefault(support, []).append(source_id)
    # Caller inputs and calculations are records, never external citations.
    input_id = "trade_input"
    calc_id = "trade_calculation"
    sources.extend([
        EvidenceSource(id=input_id, source_type="caller_input", authority_class="caller", publisher="request parameters",
                       retrieved_at=result.requested_at, source_status="provided"),
        EvidenceSource(id=calc_id, source_type="internal_engine", authority_class="internal", publisher="FACTRAIL Trade",
                       retrieved_at=result.requested_at, source_status="derived", metadata={"engine": "factrail.trade.service.assess_import"}),
    ])
    facts: list[Fact] = []
    def add(field: str, value: object, refs: list[str], kind: str, metadata: dict | None = None, support: SupportLevel | None = None) -> None:
        if value is not None:
            level = support or (SupportLevel.CALLER_INPUT if kind == "input" else
                                SupportLevel.DERIVED_PROVISIONAL if kind in ("inferred", "estimated", "derived") else
                                SupportLevel.SUPPORTED)
            facts.append(Fact(field=field, value=value, evidence_ids=list(dict.fromkeys(refs)),
                              jurisdiction=parameters.destination_country, provenance_type=kind,
                              metadata=metadata or {}, support_level=level))

    add("origin_country", parameters.origin_country, [input_id], "input", support=SupportLevel.CALLER_INPUT)
    add("destination_country", parameters.destination_country, [input_id], "input", support=SupportLevel.CALLER_INPUT)
    add("product_description", parameters.product, [input_id], "input", support=SupportLevel.CALLER_INPUT)
    add("goods_value", parameters.goods_value, [input_id], "input", {"currency": parameters.currency}, SupportLevel.CALLER_INPUT)
    add("currency", parameters.currency, [input_id], "input", support=SupportLevel.CALLER_INPUT)
    add("quantity", parameters.quantity, [input_id], "input", support=SupportLevel.CALLER_INPUT)
    if parameters.material:
        add("material", parameters.material, [input_id], "input", support=SupportLevel.CALLER_INPUT)
    if parameters.weight_kg is not None:
        add("weight_kg", parameters.weight_kg, [input_id], "input", support=SupportLevel.CALLER_INPUT)
    if parameters.dimensions:
        add("dimensions", parameters.dimensions, [input_id], "input", support=SupportLevel.CALLER_INPUT)
    if parameters.freight_cost is not None:
        add("freight_cost", parameters.freight_cost, [input_id], "input", {"currency": parameters.currency}, SupportLevel.CALLER_INPUT)
    if parameters.insurance_cost is not None:
        add("insurance_cost", parameters.insurance_cost, [input_id], "input", {"currency": parameters.currency}, SupportLevel.CALLER_INPUT)
    if parameters.known_hs_code:
        add("supplied_hs_code", parameters.known_hs_code, [input_id], "input", support=SupportLevel.CALLER_INPUT)
    classification_refs = by_support.get("classification", [calc_id])
    add("hs_code", result.classification.hs_code, [input_id, *classification_refs] if parameters.known_hs_code else classification_refs,
        "input" if parameters.known_hs_code else "inferred", {"review_required": result.classification.review_required,
        "reasoning_summary": result.classification.reasoning_summary,
        "alternatives": [item.model_dump(mode="json") for item in result.classification.alternatives]}, SupportLevel.CALLER_INPUT if parameters.known_hs_code else SupportLevel.DERIVED_PROVISIONAL)
    add("cn_code", result.classification.cn_code, by_support.get("classification", [calc_id]), "inferred", support=SupportLevel.DERIVED_PROVISIONAL)
    add("taric_code", result.classification.taric_code, by_support.get("classification", [calc_id]), "inferred", support=SupportLevel.DERIVED_PROVISIONAL)
    official_records = [item for item in result.sources if "official_taric_snapshot" in item.supports and isinstance(item.value, dict)]
    if official_records:
        record = official_records[-1]
        record_ref = next((source.id for source in sources
                           if source.publisher == record.source and source.metadata.get("source_outcome") == record.source_outcome), calc_id)
        if record.value.get("measures"):
            add("customs_measures", record.value["measures"], [record_ref], "sourced",
                {"classification": record.value.get("classification"),
                 "snapshot": record.value.get("snapshot"),
                 "source_outcome": record.value.get("source_outcome"),
                 "source_detail": record.value.get("source_detail")}, SupportLevel.AUTHORITATIVE)
    duty_refs = [id for id in by_support.get("duty", []) if next(s for s in sources if s.id == id).source_status not in ("derived", "unavailable")]
    duty_kind = "estimated" if any(next(s for s in sources if s.id == id).source_status == "estimated" for id in duty_refs) else ("sourced" if duty_refs else "derived")
    duty_source_types = {next(s for s in sources if s.id == sid).source_type for sid in duty_refs}
    duty_support = (SupportLevel.AUTHORITATIVE if "official_customs_source" in duty_source_types else
                    SupportLevel.SUPPORTED if "access2markets_secondary" in duty_source_types else
                    SupportLevel.CURATED if duty_refs else SupportLevel.SUPPORTED)
    add("base_duty_rate_pct", result.customs.base_duty_rate_pct, duty_refs or [calc_id], duty_kind,
        {"rate_type": result.customs.applied_rate_type}, duty_support)
    add("preferential_duty_rate_pct", result.customs.preferential_rate_pct, by_support.get("duty", []) or [calc_id], "sourced", support=duty_support)
    vat_refs = [id for id in by_support.get("vat", []) if next(s for s in sources if s.id == id).source_status not in ("derived", "unavailable")]
    vat_support = SupportLevel.CURATED if vat_refs else SupportLevel.SUPPORTED
    add("import_vat_rate_pct", result.tax.import_vat_rate_pct, vat_refs or [calc_id], "sourced" if vat_refs else "derived", support=vat_support)
    for field, value, refs, formula, inputs in [
        ("estimated_duty", result.customs.estimated_duty, duty_refs, "customs_value × applied_duty_rate_pct / 100", ["goods_value", "freight_cost", "insurance_cost", "applied_duty_rate_pct"]),
        ("estimated_import_vat", result.tax.estimated_import_vat, vat_refs, "VAT base × import_vat_rate_pct / 100", ["goods_value", "freight_cost", "insurance_cost", "estimated_duty", "import_vat_rate_pct"]),
        ("estimated_total", result.landed_cost.estimated_total, duty_refs + vat_refs, "goods_value + freight + insurance + duty + VAT + other known costs", ["goods_value", "freight_cost", "insurance_cost", "estimated_duty", "estimated_import_vat"]),
    ]:
        provisional_inputs = []
        if duty_support == SupportLevel.CURATED or result.customs.base_duty_rate_pct is None:
            provisional_inputs.append("applied_duty_rate_pct")
        if vat_support == SupportLevel.CURATED:
            provisional_inputs.append("import_vat_rate_pct")
        add(field, value, [calc_id, input_id, *refs], "derived", {"formula": formula, "derivation": {"method": {"estimated_duty": "factrail_customs_duty_v1", "estimated_import_vat": "factrail_import_vat_v1", "estimated_total": "factrail_landed_cost_v1"}[field], "inputs": inputs, "provisional_inputs": provisional_inputs},
            "engine": "factrail.trade.service.assess_import", "currency": parameters.currency,
            "assumptions": result.landed_cost.assumptions if field == "estimated_total" else []},
            SupportLevel.DERIVED_PROVISIONAL if provisional_inputs else SupportLevel.DERIVED_SUPPORTED)
    for req in result.compliance.requirements + result.compliance.restrictions:
        refs = by_support.get("compliance", [])
        add("compliance_requirement", req.model_dump(mode="json", exclude={"evidence"}), refs or [calc_id],
            "sourced" if req.evidence else "inferred")
    if result.compliance.warnings:
        add("compliance_findings", result.compliance.warnings, [calc_id], "derived")
    landed_value = result.landed_cost.model_dump(mode="json", exclude={"evidence"})
    add("landed_cost", landed_value, [calc_id, input_id, *duty_refs, *vat_refs], "derived",
        {"derivation": {"method": "factrail_landed_cost_v1", "inputs": ["goods_value", "freight_cost", "insurance_cost", "estimated_duty", "estimated_import_vat"], "provisional_inputs": [name for name, provisional in (("base_duty_rate_pct", duty_support == SupportLevel.CURATED or result.customs.base_duty_rate_pct is None), ("import_vat_rate_pct", vat_support == SupportLevel.CURATED)) if provisional]}, "currency": parameters.currency}, SupportLevel.DERIVED_PROVISIONAL if duty_support == SupportLevel.CURATED or result.customs.base_duty_rate_pct is None or vat_support == SupportLevel.CURATED else SupportLevel.DERIVED_SUPPORTED)
    unresolved = sorted({field.field for field in result.missing_information})
    if not result.classification.hs_code:
        unresolved.append("hs_code")
    if result.customs.base_duty_rate_pct is None:
        unresolved.append("base_duty_rate_pct")
    elif any(s.metadata.get("source_outcome") in ("source_error", "source_unavailable", "source_not_integrated", "lookup_disabled", "partial") for s in sources):
        unresolved.append("authoritative_duty_rate")
    if result.tax.import_vat_rate_pct is None:
        unresolved.append("import_vat_rate_pct")
    unresolved = sorted(set(unresolved))
    has_classification = bool(result.classification.hs_code)
    tariff_outcome = next((s.metadata.get("source_outcome") for s in sources if s.source_type == "access2markets_secondary" and s.metadata.get("source_outcome")), None)
    if tariff_outcome is None:
        tariff_outcome = "not_required" if not has_classification else ("success" if result.customs.applied_rate_type == "third_country_mfn" else "source_not_integrated")
    official_record = next((item for item in result.sources if "official_taric_snapshot" in item.supports), None)
    official_value = official_record.value if official_record and isinstance(official_record.value, dict) else {}
    official_outcome = official_value.get("source_outcome", "source_unavailable")
    official_detail = official_value.get("source_detail", "snapshot_not_installed")
    a2m_detail = next((s.metadata.get("source_detail") for s in sources if s.metadata.get("source_detail") and s.source_type == "access2markets_secondary"), "not_required")
    source_outcomes = {"tariff_registry": tariff_outcome, "access2markets_secondary": tariff_outcome,
                       "official_taric_snapshot": official_outcome}
    source_details = {"tariff_registry": a2m_detail, "access2markets_secondary": a2m_detail,
                      "official_taric_snapshot": official_detail}
    failures = [name for name in ("access2markets_secondary", "official_taric_snapshot") if source_outcomes[name] == "source_error"]
    requested = ["hs_code", "base_duty_rate_pct", "import_vat_rate_pct", "estimated_total"]
    resolved = [field for field in requested if any(f.field == field for f in facts)]
    reason_map = {}
    missing_fields = {item.field for item in result.missing_information}
    for field in unresolved:
        if field in missing_fields:
            reason_map[field] = "missing_user_input"
        elif field in ("hs_code", "authoritative_duty_rate"):
            source_reason = (source_outcomes.get("official_taric_snapshot", "source_unavailable")
                             if field == "authoritative_duty_rate" else source_outcomes.get("tariff_registry", "source_unavailable"))
            reason_map[field] = ("classification_required" if field == "hs_code" and (result.classification.review_required or not result.classification.hs_code)
                                 else "source_unavailable" if source_reason == "partial" else source_reason)
        elif field == "base_duty_rate_pct":
            reason_map[field] = source_outcomes.get("tariff_registry", "source_unavailable")
        elif field == "import_vat_rate_pct":
            reason_map[field] = "source_unavailable"
        else:
            reason_map[field] = "unsupported_field"
    level = CoverageLevel.SUFFICIENT if not unresolved and len(resolved) == len(requested) else (CoverageLevel.PARTIAL if resolved else CoverageLevel.INSUFFICIENT)
    retrieved = [s.retrieved_at for s in sources if s.source_type not in ("caller_input", "internal_engine") and s.source_status != "unavailable"]
    stale_evidence = any(detail in ("stale_cache_after_refresh_failure", "stale_snapshot") for detail in source_details.values())
    return EvidenceEnvelope(status=VerificationStatus.SUPPORTED if level == CoverageLevel.SUFFICIENT else VerificationStatus.INSUFFICIENT_EVIDENCE,
        subject=Subject(type="import", name=parameters.product, identifiers={"origin_country": parameters.origin_country,
            "destination_country": parameters.destination_country, **({"hs_code": parameters.known_hs_code} if parameters.known_hs_code else {})}),
        facts=facts, evidence=sources, conflicts=[],
        coverage=Coverage(level=level, fields_requested=requested, fields_resolved=resolved,
            fields_unresolved=unresolved, metadata={"trade_status": result.status.value, "source_failures": failures,
                "source_outcomes": source_outcomes, "required_missing_input": [],
                "source_details": source_details,
                "official_taric_snapshot": {"source": "EU_TARIC", "outcome": official_outcome,
                    "detail": official_detail, "classification": official_value.get("classification"),
                    "snapshot": official_value.get("snapshot")},
                "missing_user_input_fields": sorted(missing_fields),
                "source_errors": [s.metadata.get("note") for s in sources if s.metadata.get("source_outcome") == "source_error"],
                "unresolved_field_reasons": reason_map}),
        freshness=Freshness(generated_at=now, oldest_supporting_retrieved_at=min(retrieved) if retrieved else None,
            newest_supporting_retrieved_at=max(retrieved) if retrieved else None, stale=stale_evidence), generated_at=now)


def assess_import_evidence(parameters: AssessImportInput) -> EvidenceEnvelope:
    return import_to_envelope(parameters, assess_import(parameters))
