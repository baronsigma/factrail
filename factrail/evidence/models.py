"""Versioned, source-traceable verification contract."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class VerificationStatus(str, Enum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    STALE = "stale"
    CONFLICTING_SOURCES = "conflicting_sources"


class CoverageLevel(str, Enum):
    SUFFICIENT = "sufficient"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"


class Subject(BaseModel):
    type: str
    name: str | None = None
    identifiers: dict[str, str] = Field(default_factory=dict)


class EvidenceSource(BaseModel):
    id: str
    source_type: str
    authority_class: str
    publisher: str
    url: str | None = None
    retrieved_at: datetime
    effective_at: datetime | None = None
    content_hash: str | None = None
    source_status: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Fact(BaseModel):
    field: str
    value: Any
    normalized_value: Any = None
    effective_at: datetime | None = None
    evidence_ids: list[str] = Field(min_length=1)
    jurisdiction: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    provenance_type: str = "sourced"


class ConflictSide(BaseModel):
    value: Any
    evidence_ids: list[str] = Field(min_length=1)


class Conflict(BaseModel):
    field: str
    sides: list[ConflictSide] = Field(min_length=2)
    resolution_status: str
    resolution_reason: str | None = None


class Coverage(BaseModel):
    level: CoverageLevel
    fields_requested: list[str]
    fields_resolved: list[str]
    fields_unresolved: list[str]
    metadata: dict[str, Any] = Field(default_factory=dict)


class Freshness(BaseModel):
    generated_at: datetime
    oldest_supporting_retrieved_at: datetime | None = None
    newest_supporting_retrieved_at: datetime | None = None
    stale: bool
    recheck_after: datetime | None = None


class EvidenceEnvelope(BaseModel):
    schema_version: str = "1.1"
    status: VerificationStatus
    subject: Subject
    facts: list[Fact]
    evidence: list[EvidenceSource]
    conflicts: list[Conflict]
    coverage: Coverage
    freshness: Freshness
    receipt_id: str | None = None
    state_fingerprint: str | None = None
    generated_at: datetime

    @model_validator(mode="after")
    def check_references(self) -> "EvidenceEnvelope":
        ids = [source.id for source in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("evidence IDs must be unique")
        valid = set(ids)
        for refs in [f.evidence_ids for f in self.facts] + [side.evidence_ids for c in self.conflicts for side in c.sides]:
            if not set(refs) <= valid:
                raise ValueError("fact or conflict references missing evidence")
        return self
