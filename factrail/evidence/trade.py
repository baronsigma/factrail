"""Adapt the existing import engine without changing its calculations."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from factrail.trade.models import AssessImportInput, ImportAssessment, ProvenanceStatus
from factrail.trade.service import assess_import

from .models import Coverage, CoverageLevel, EvidenceEnvelope, EvidenceSource, Fact, Freshness, Subject, VerificationStatus


def import_to_envelope(parameters: AssessImportInput, result: ImportAssessment) -> EvidenceEnvelope:
    now = datetime.now(timezone.utc)
    sources: list[EvidenceSource] = []
    by_support: dict[str, list[str]] = {}
    for item in result.sources:
        key = json.dumps({"value": item.value, "status": item.status.value, "authority": item.authority,
                          "source": item.source, "url": item.url, "supports": sorted(item.supports)},
                         sort_keys=True, default=str)
        source_id = "trade_" + hashlib.sha256(key.encode()).hexdigest()[:24]
        if source_id in {s.id for s in sources}:
            continue
        internal = item.status in (ProvenanceStatus.DERIVED, ProvenanceStatus.INFERRED)
        sources.append(EvidenceSource(
            id=source_id, source_type="internal_engine" if internal else "curated_or_external_reference",
            authority_class="internal" if internal else ("official_derived_dataset" if item.status == ProvenanceStatus.VERIFIED else "secondary"),
            publisher=item.source, url=None if internal else (item.url or None), retrieved_at=item.retrieved_at,
            source_status=item.status.value,
            metadata={"authority": item.authority, "effective_date_text": item.effective_date,
                      "note": item.note, "assertion_value": item.value, "supports": item.supports},
        ))
        for support in item.supports:
            by_support.setdefault(support, []).append(source_id)
    if result.customs.applied_rate_type and "fallback" in result.customs.applied_rate_type:
        sources.append(EvidenceSource(id="trade_taric_unavailable", source_type="tariff_registry",
            authority_class="primary_official_registry", publisher="EU TARIC",
            retrieved_at=result.requested_at, source_status="unavailable",
            metadata={"reason": "Trade engine used curated fallback instead of authoritative tariff data"}))
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
    def add(field: str, value: object, refs: list[str], kind: str, metadata: dict | None = None) -> None:
        if value is not None:
            facts.append(Fact(field=field, value=value, evidence_ids=list(dict.fromkeys(refs)),
                              jurisdiction=parameters.destination_country, provenance_type=kind,
                              metadata=metadata or {}))

    add("origin_country", parameters.origin_country, [input_id], "input")
    add("destination_country", parameters.destination_country, [input_id], "input")
    add("goods_value", parameters.goods_value, [input_id], "input", {"currency": parameters.currency})
    add("quantity", parameters.quantity, [input_id], "input")
    if parameters.known_hs_code:
        add("supplied_hs_code", parameters.known_hs_code, [input_id], "input")
    add("hs_code", result.classification.hs_code, [input_id, *by_support.get("classification", [calc_id])] if parameters.known_hs_code else by_support.get("classification", [calc_id]), "inferred" if not parameters.known_hs_code else "normalized",
        {"review_required": result.classification.review_required})
    add("cn_code", result.classification.cn_code, by_support.get("classification", [calc_id]), "inferred")
    add("taric_code", result.classification.taric_code, by_support.get("classification", [calc_id]), "inferred")
    duty_refs = [id for id in by_support.get("duty", []) if next(s for s in sources if s.id == id).source_status not in ("derived", "unavailable")]
    duty_kind = "estimated" if any(next(s for s in sources if s.id == id).source_status == "estimated" for id in duty_refs) else ("sourced" if duty_refs else "derived")
    add("base_duty_rate_pct", result.customs.base_duty_rate_pct, duty_refs or [calc_id], duty_kind,
        {"rate_type": result.customs.applied_rate_type})
    vat_refs = [id for id in by_support.get("vat", []) if next(s for s in sources if s.id == id).source_status not in ("derived", "unavailable")]
    add("import_vat_rate_pct", result.tax.import_vat_rate_pct, vat_refs or [calc_id], "sourced" if vat_refs else "derived")
    for field, value, refs, formula, inputs in [
        ("estimated_duty", result.customs.estimated_duty, duty_refs, "customs_value × applied_duty_rate_pct / 100", ["goods_value", "freight_cost", "insurance_cost", "applied_duty_rate_pct"]),
        ("estimated_import_vat", result.tax.estimated_import_vat, vat_refs, "VAT base × import_vat_rate_pct / 100", ["goods_value", "freight_cost", "insurance_cost", "estimated_duty", "import_vat_rate_pct"]),
        ("estimated_total", result.landed_cost.estimated_total, duty_refs + vat_refs, "goods_value + freight + insurance + duty + VAT + other known costs", ["goods_value", "freight_cost", "insurance_cost", "estimated_duty", "estimated_import_vat"]),
    ]:
        add(field, value, [calc_id, input_id, *refs], "derived", {"formula": formula, "inputs": inputs,
            "engine": "factrail.trade.service.assess_import", "currency": parameters.currency,
            "assumptions": result.landed_cost.assumptions if field == "estimated_total" else []})
    for req in result.compliance.requirements + result.compliance.restrictions:
        refs = by_support.get("compliance", [])
        add("compliance_requirement", req.model_dump(mode="json", exclude={"evidence"}), refs or [calc_id],
            "sourced" if req.evidence else "inferred")
    unresolved = sorted({field.field for field in result.missing_information})
    if result.customs.base_duty_rate_pct is None:
        unresolved.append("base_duty_rate_pct")
    elif any(s.id == "trade_taric_unavailable" for s in sources):
        unresolved.append("authoritative_duty_rate")
    if result.tax.import_vat_rate_pct is None:
        unresolved.append("import_vat_rate_pct")
    unresolved = sorted(set(unresolved))
    failures = sorted({s.publisher for s in sources if s.source_status == "unavailable"})
    requested = ["hs_code", "base_duty_rate_pct", "import_vat_rate_pct", "estimated_total"]
    resolved = [field for field in requested if any(f.field == field for f in facts)]
    level = CoverageLevel.SUFFICIENT if not unresolved and len(resolved) == len(requested) else (CoverageLevel.PARTIAL if resolved else CoverageLevel.INSUFFICIENT)
    retrieved = [s.retrieved_at for s in sources if s.source_type not in ("caller_input", "internal_engine") and s.source_status != "unavailable"]
    return EvidenceEnvelope(status=VerificationStatus.SUPPORTED if level == CoverageLevel.SUFFICIENT else VerificationStatus.INSUFFICIENT_EVIDENCE,
        subject=Subject(type="import", name=parameters.product, identifiers={"origin_country": parameters.origin_country,
            "destination_country": parameters.destination_country, **({"hs_code": parameters.known_hs_code} if parameters.known_hs_code else {})}),
        facts=facts, evidence=sources, conflicts=[],
        coverage=Coverage(level=level, fields_requested=requested, fields_resolved=resolved,
            fields_unresolved=unresolved, metadata={"trade_status": result.status.value, "source_failures": failures}),
        freshness=Freshness(generated_at=now, oldest_supporting_retrieved_at=min(retrieved) if retrieved else None,
            newest_supporting_retrieved_at=max(retrieved) if retrieved else None, stale=False), generated_at=now)


def assess_import_evidence(parameters: AssessImportInput) -> EvidenceEnvelope:
    return import_to_envelope(parameters, assess_import(parameters))
