"""Adapt the existing French company result into the Evidence Contract."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from typing import Callable

from factrail.models import FrenchCompany
from factrail.sources.insee import InseeAdapter

from .models import (Coverage, CoverageLevel, EvidenceEnvelope, EvidenceSource,
                     Fact, Freshness, Subject, VerificationStatus, SupportLevel)
from .authority import authority_policy_registry

DEFAULT_FIELDS = ("status", "legal_name", "legal_form", "head_office", "naf_code", "siren")
FIELD_MAP = {
    "status": "status", "legal_name": "legal_name", "legal_form": "legal_form_code",
    "head_office": "address", "naf_code": "naf_code", "siren": "siren",
    "siret": "siret", "trade_name": "trade_name", "creation_date": "creation_date",
    "cessation_date": "cessation_date", "diffusion_status": "diffusion_status",
}


def resolve_company_fr(identifier: str, fields: list[str] | None = None,
                       lookup: Callable[[str], FrenchCompany] | None = None) -> EvidenceEnvelope:
    if not isinstance(identifier, str) or not identifier.isdigit() or len(identifier) not in (9, 14):
        raise ValueError("identifier must be a 9-digit SIREN or 14-digit SIRET")
    requested = list(dict.fromkeys(fields if fields is not None else DEFAULT_FIELDS))
    if any(field not in FIELD_MAP for field in requested):
        raise ValueError("unsupported company field")
    company = (lookup or InseeAdapter().lookup_with_bodacc)(identifier)
    now = datetime.now(timezone.utc)
    sources = [EvidenceSource(
        id=source.id, source_type="government_registry", authority_class="primary_official_registry",
        publisher=source.name, url=source.url, retrieved_at=source.retrieved_at,
        source_status="available") for source in company.sources]
    events = company.events or {}
    if events:
        sources.append(EvidenceSource(
            id="bodacc", source_type="government_bulletin", authority_class="official_publication",
            publisher="DILA BODACC", url="https://bodacc-datadila.opendatasoft.com",
            retrieved_at=company.checked_at, source_status=events.get("source_status", "unknown"),
            metadata={"source_error": events.get("source_error"), "truncated": events.get("truncated")},
        ))
    insee_ids = [s.id for s in sources if s.id.startswith("insee")]
    facts: list[Fact] = []
    resolved: list[str] = []
    for field in requested:
        value = getattr(company, FIELD_MAP[field])
        if value is None:
            continue
        refs = ["insee-siret"] if field in ("head_office", "siret") and "insee-siret" in insee_ids else insee_ids[:1]
        if not refs:
            continue
        facts.append(Fact(field=field, value=value.model_dump(mode="json") if hasattr(value, "model_dump") else value,
                          evidence_ids=refs, jurisdiction="FR", support_level=SupportLevel.AUTHORITATIVE))
        resolved.append(field)
    # Historical notices remain historical facts; they never override current INSEE status.
    if "bodacc" in {s.id for s in sources} and events.get("source_status") == "available":
        for notice in events.get("historical_removal_notices") or []:
            facts.append(Fact(field="historical_removal_notice", value=notice,
                              evidence_ids=["bodacc"], jurisdiction="FR", support_level=SupportLevel.AUTHORITATIVE))
    unresolved = [field for field in requested if field not in resolved]
    reasons = {}
    for field in unresolved:
        # Missing current fields are normally absent in Sirene payloads; if
        # the registry source itself failed, expose that distinct cause.
        registry_failed = any(s.source_type == "government_registry" and s.source_status == "unavailable" for s in sources)
        reasons[field] = "source_unavailable" if registry_failed else "source_error" if any(s.source_type == "government_registry" and s.source_status == "error" for s in sources) else "not_applicable" if field == "cessation_date" and getattr(company, FIELD_MAP[field]) is None and getattr(company, "status", None) == "active" else "unsupported_fact"
    level = CoverageLevel.SUFFICIENT if not unresolved else (CoverageLevel.PARTIAL if resolved else CoverageLevel.INSUFFICIENT)
    available = [s.retrieved_at for s in sources if s.source_status == "available"]
    ttl_seconds = int(os.environ.get("FACTRAIL_CACHE_TTL", "3600"))
    stale = bool(available and max(available) < now - timedelta(seconds=ttl_seconds))
    status = (VerificationStatus.STALE if stale else VerificationStatus.SUPPORTED) if level == CoverageLevel.SUFFICIENT else VerificationStatus.INSUFFICIENT_EVIDENCE
    return EvidenceEnvelope(
        status=status, subject=Subject(type="company_fr", name=company.legal_name,
            identifiers={k: v for k, v in (("siren", company.siren), ("siret", company.siret)) if v}),
        facts=facts, evidence=sources, conflicts=[],
        coverage=Coverage(level=level, fields_requested=requested, fields_resolved=resolved,
            fields_unresolved=unresolved, metadata={"bodacc_source_status": events.get("source_status", "absent"),
            "source_outcomes": {"government_bulletin": ("success" if events.get("source_status") == "available" else "source_error" if events.get("source_status") == "unavailable" else "lookup_disabled" if events.get("source_status") == "disabled" else "not_required")},
            "unresolved_field_reasons": reasons, "authority_policies": [r.model_dump(mode="json") for r in authority_policy_registry()]}),
        freshness=Freshness(generated_at=now, oldest_supporting_retrieved_at=min(available) if available else None,
            newest_supporting_retrieved_at=max(available) if available else None, stale=stale),
        generated_at=now)
