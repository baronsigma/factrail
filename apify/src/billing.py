"""Conservative eligibility for one custom PPE event per Actor run."""
from __future__ import annotations

import re

RECEIPT = re.compile(r'fr_[0-9a-f]{64}\Z')
EVENTS = {'verify': 'factrail-verify', 'assess_import': 'factrail-assess-import'}


def billable_event(action: str, envelope: object) -> str | None:
    if action not in EVENTS or not isinstance(envelope, dict):
        return None
    receipt = envelope.get('receipt_id')
    if not isinstance(receipt, str) or RECEIPT.fullmatch(receipt) is None:
        return None
    if envelope.get('schema_version') != '1.1':
        return None
    status = envelope.get('status')
    if action == 'verify' and status != 'supported':
        return None
    if action == 'assess_import' and status != 'supported':
        return None
    coverage = envelope.get('coverage')
    if not isinstance(coverage, dict) or coverage.get('level') != 'sufficient':
        return None
    if coverage.get('fields_unresolved') or (coverage.get('metadata') or {}).get('source_failures'):
        return None
    if not set(coverage.get('fields_requested') or []) <= set(coverage.get('fields_resolved') or []):
        return None
    if (envelope.get('freshness') or {}).get('stale') is not False:
        return None
    if any(conflict.get('resolution_status') != 'resolved' for conflict in envelope.get('conflicts', [])):
        return None
    evidence = envelope.get('evidence')
    if not isinstance(evidence, list) or not evidence:
        return None
    by_id = {source.get('id'): source for source in evidence if isinstance(source, dict)}
    facts = envelope.get('facts')
    if not isinstance(facts, list):
        return None
    for field in coverage.get('fields_requested') or []:
        if not any(isinstance(fact, dict) and fact.get('field') == field for fact in facts):
            return None
    for fact in facts:
        refs = fact.get('evidence_ids') if isinstance(fact, dict) else None
        if not isinstance(refs, list) or not refs:
            return None
        if any(ref not in by_id or by_id[ref].get('source_status') not in
               {'available', 'verified', 'provided', 'derived'} for ref in refs):
            return None
    return EVENTS[action]
