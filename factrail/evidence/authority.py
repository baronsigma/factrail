"""Field-scoped authority and conflict rules; no universal source ranking."""
from __future__ import annotations

import json
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from .models import Conflict, ConflictSide, EvidenceSource, Fact, VerificationStatus


class AuthorityClass(str, Enum):
    PRIMARY_OFFICIAL_REGISTRY = "primary_official_registry"
    OFFICIAL_PUBLICATION = "official_publication"
    OFFICIAL_DERIVED_DATASET = "official_derived_dataset"
    COMMERCIAL = "commercial"
    SECONDARY = "secondary"
    CURATED_REFERENCE = "curated_reference"
    HEURISTIC_CLASSIFIER = "heuristic_classifier"


class AuthorityRule(BaseModel):
    policy_id: str
    domain: str
    field: str
    jurisdiction: str | None = None
    precedence: list[AuthorityClass] = Field(min_length=2)
    applicable_from: datetime | None = None
    applicable_until: datetime | None = None
    reason: str

    def applies(self, domain: str, field: str, jurisdiction: str | None, at: datetime | None) -> bool:
        if (domain, field) != (self.domain, self.field):
            return False
        if self.jurisdiction is not None and self.jurisdiction != jurisdiction:
            return False
        if at is None:
            return self.applicable_from is None and self.applicable_until is None
        return ((self.applicable_from is None or at >= self.applicable_from)
                and (self.applicable_until is None or at <= self.applicable_until))


class AuthorityDecision(BaseModel):
    selected: Fact | None
    conflict: Conflict | None
    status: VerificationStatus


# Source semantics inspected in InseeAdapter.lookup_with_bodacc: INSEE/Sirene
# is the current register; BODACC notices are official publications but may be
# historical and are deliberately not used to overwrite current registry facts.
COMPANY_FR_AUTHORITY_POLICIES = [
    AuthorityRule(policy_id="company_fr.status.current_registry_over_publication", domain="company_fr",
        field="status", jurisdiction="FR",
        precedence=[AuthorityClass.PRIMARY_OFFICIAL_REGISTRY, AuthorityClass.OFFICIAL_PUBLICATION],
        reason="INSEE/Sirene reports current administrative status; BODACC notices are historical publication evidence."),
    AuthorityRule(policy_id="company_fr.legal_name.registry_over_publication", domain="company_fr",
        field="legal_name", jurisdiction="FR",
        precedence=[AuthorityClass.PRIMARY_OFFICIAL_REGISTRY, AuthorityClass.OFFICIAL_PUBLICATION],
        reason="INSEE/Sirene is the current legal-entity register; BODACC is publication evidence and does not maintain the current registered name."),
]

# Customs-source policy. TARIC is an official Commission data product, not
# binding legislation itself; measure records preserve legal references, while
# the applicable legal act/Official Journal remains the ultimate legal source.
TRADE_IMPORT_AUTHORITY_POLICIES = [
    AuthorityRule(policy_id="trade_import.eu_customs_measure.taric_over_secondary_and_curated",
        domain="trade_import", field="customs_measures", jurisdiction="EU",
        precedence=[AuthorityClass.OFFICIAL_DERIVED_DATASET, AuthorityClass.SECONDARY, AuthorityClass.CURATED_REFERENCE],
        reason="An installed official Commission TARIC snapshot is primary customs-measure data; Access2Markets is secondary; FACTRAIL reference rates are explicit curated fallback."),
    AuthorityRule(policy_id="trade_import.eu_nomenclature.official_over_heuristic",
        domain="trade_import", field="taric_code", jurisdiction="EU",
        precedence=[AuthorityClass.OFFICIAL_DERIVED_DATASET, AuthorityClass.HEURISTIC_CLASSIFIER],
        reason="Official TARIC/CN nomenclature and declarable-code status take precedence over rule-based classification candidates, which remain provisional."),
]


def authority_policy_registry() -> list[AuthorityRule]:
    """Return a copy of the documented, field-scoped policies."""
    return [rule.model_copy(deep=True) for rule in COMPANY_FR_AUTHORITY_POLICIES + TRADE_IMPORT_AUTHORITY_POLICIES]


def resolve_assertions(facts: list[Fact], sources: list[EvidenceSource], *, domain: str,
                       field: str, jurisdiction: str | None = None, at: datetime | None = None,
                       rules: list[AuthorityRule] | None = None) -> AuthorityDecision:
    """Resolve only a rule's domain/field. Preserve every assertion in the caller."""
    candidates = [f for f in facts if f.field == field and (jurisdiction is None or f.jurisdiction == jurisdiction)]
    if not candidates:
        return AuthorityDecision(selected=None, conflict=None, status=VerificationStatus.INSUFFICIENT_EVIDENCE)
    def key(f: Fact) -> str:
        return json.dumps(f.normalized_value if f.normalized_value is not None else f.value,
                          sort_keys=True, ensure_ascii=False, default=str)
    groups: dict[str, list[Fact]] = {}
    for fact in candidates:
        groups.setdefault(key(fact), []).append(fact)
    if len(groups) == 1:
        return AuthorityDecision(selected=candidates[0], conflict=None, status=VerificationStatus.SUPPORTED)
    source_by_id = {s.id: s for s in sources}
    sides = [ConflictSide(value=items[0].normalized_value if items[0].normalized_value is not None else items[0].value,
                          evidence_ids=sorted({ref for item in items for ref in item.evidence_ids}))
             for _, items in sorted(groups.items())]
    rule = next((r for r in rules or [] if r.applies(domain, field, jurisdiction, at)), None)
    if rule:
        ranked = []
        for items in groups.values():
            classes = {source_by_id[ref].authority_class for item in items for ref in item.evidence_ids}
            rank = min((rule.precedence.index(AuthorityClass(cls)) for cls in classes
                        if cls in rule.precedence), default=len(rule.precedence))
            ranked.append((rank, items[0]))
        ranked.sort(key=lambda pair: pair[0])
        if len(ranked) == 1 or ranked[0][0] < ranked[1][0]:
            return AuthorityDecision(selected=ranked[0][1],
                conflict=Conflict(field=field, sides=sides, resolution_status="resolved",
                    resolution_reason=f"{rule.policy_id}: {rule.reason}"), status=VerificationStatus.SUPPORTED)
    return AuthorityDecision(selected=None,
        conflict=Conflict(field=field, sides=sides, resolution_status="unresolved",
            resolution_reason=(f"{rule.policy_id}: top-ranked evidence is tied or insufficiently authoritative" if rule else "No applicable authority policy; conflicting evidence retained")),
        status=VerificationStatus.CONFLICTING_SOURCES)
