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
        conflict=Conflict(field=field, sides=sides, resolution_status="unresolved"),
        status=VerificationStatus.CONFLICTING_SOURCES)
