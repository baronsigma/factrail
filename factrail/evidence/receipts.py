"""Content-addressed receipt IDs and durable SQLite storage."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone

from .models import EvidenceEnvelope


def receipt_id_for(envelope: EvidenceEnvelope) -> str:
    """Hash all envelope fields except receipt_id, generated_at and freshness.generated_at.

    Source retrieval/effective times remain in the hash: a fresh observation is
    distinct evidence. Canonical JSON sorts keys and uses compact separators.
    """
    payload = envelope.model_dump(mode="json", exclude={"receipt_id", "state_fingerprint", "generated_at"})
    # The 1.0 contract had no provenance_type; retain its exact canonical bytes.
    if envelope.schema_version == "1.0":
        for fact in payload["facts"]:
            fact.pop("provenance_type", None)
    payload["freshness"].pop("generated_at", None)
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return "fr_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def state_fingerprint_for(envelope: EvidenceEnvelope) -> str:
    """Hash substantive state, independent of observation times and ordering."""
    def canonical(value: object) -> str:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

    evidence = {source.id: source for source in envelope.evidence}
    def source_keys(ids: list[str]) -> list[dict]:
        return sorted(({
            "source_type": evidence[id].source_type, "authority_class": evidence[id].authority_class,
            "publisher": evidence[id].publisher, "url": evidence[id].url,
        } for id in ids), key=canonical)

    facts = [{
        "field": fact.field, "value": fact.normalized_value if fact.normalized_value is not None else fact.value,
        "effective_at": fact.effective_at.isoformat() if fact.effective_at else None,
        "jurisdiction": fact.jurisdiction, "provenance_type": fact.provenance_type,
        "metadata": fact.metadata, "sources": source_keys(fact.evidence_ids),
    } for fact in envelope.facts]
    conflicts = [{
        "field": conflict.field, "resolution_status": conflict.resolution_status,
        "resolution_reason": conflict.resolution_reason,
        "sides": sorted(({"value": side.value, "sources": source_keys(side.evidence_ids)}
                         for side in conflict.sides), key=canonical),
    } for conflict in envelope.conflicts]
    payload = {
        "subject": envelope.subject.model_dump(mode="json"),
        "facts": sorted(facts, key=canonical), "conflicts": sorted(conflicts, key=canonical),
        "coverage": {"level": envelope.coverage.level.value,
                     "fields_requested": sorted(set(envelope.coverage.fields_requested)),
                     "fields_resolved": sorted(set(envelope.coverage.fields_resolved)),
                     "fields_unresolved": sorted(set(envelope.coverage.fields_unresolved))},
    }
    return "fs_" + hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


class ReceiptRepository:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or os.environ.get("FACTRAIL_CACHE_PATH", "/tmp/factrail_cache.db")
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS evidence_receipts (
                receipt_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL,
                envelope TEXT NOT NULL, created_at TEXT NOT NULL,
                subject_type TEXT NOT NULL, primary_identifier TEXT)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_receipt_subject ON evidence_receipts(subject_type, primary_identifier)")

    def save(self, envelope: EvidenceEnvelope) -> EvidenceEnvelope:
        receipt_id = receipt_id_for(envelope)
        completed = envelope.model_copy(update={"receipt_id": receipt_id,
                                                "state_fingerprint": state_fingerprint_for(envelope)})
        identifier = next(iter(completed.subject.identifiers.values()), None)
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("""INSERT OR IGNORE INTO evidence_receipts
                (receipt_id, schema_version, envelope, created_at, subject_type, primary_identifier)
                VALUES (?, ?, ?, ?, ?, ?)""", (
                receipt_id, completed.schema_version, completed.model_dump_json(),
                datetime.now(timezone.utc).isoformat(), completed.subject.type, identifier,
            ))
        return completed

    def get(self, receipt_id: str) -> EvidenceEnvelope | None:
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            row = conn.execute("SELECT envelope FROM evidence_receipts WHERE receipt_id = ?", (receipt_id,)).fetchone()
        if not row:
            return None
        envelope = EvidenceEnvelope.model_validate_json(row[0])
        if envelope.state_fingerprint is None:
            envelope = envelope.model_copy(update={"state_fingerprint": state_fingerprint_for(envelope)})
        return envelope
