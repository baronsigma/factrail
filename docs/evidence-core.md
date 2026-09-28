# FACTRAIL Evidence Core

FACTRAIL package version 2.4.0 emits EvidenceEnvelope contract `1.2`; the contract version is independent of the package version. The current JSON Schema is [`schemas/evidence-envelope-1.2.schema.json`](../schemas/evidence-envelope-1.2.schema.json).

## Contract

`EvidenceEnvelope` contains `schema_version`, `status`, `subject`, `facts`, `evidence`, `conflicts`, `coverage`, `freshness`, `receipt_id`, `state_fingerprint`, and `generated_at`. Status includes `supported`, `contradicted`, `insufficient_evidence`, `stale`, and `conflicting_sources`. Facts cite source record IDs and have support levels (`authoritative`, `supported`, `derived_supported`, `derived_provisional`, `curated`, or `caller_input`). These are not confidence percentages. Caller input is not external verification; derived values inherit the quality of inputs.

Company verification uses INSEE/Sirene and BODACC. Current registry data takes precedence over publication evidence for current status and legal name; lower-priority assertions remain visible. Trade assessment is Beta. Access2Markets is secondary evidence, curated references remain curated/provisional, and official Commission TARIC is unavailable unless a genuine snapshot is installed. The snapshot ingestion foundation is documented in [the operator guide](taric-v2.4b-operator.md).

## IDs and receipts

`receipt_id` is `fr_` plus SHA-256 of canonical JSON (sorted keys, UTF-8, compact separators, no NaN). All envelope fields participate except `receipt_id`, `state_fingerprint`, top-level `generated_at`, and `freshness.generated_at`; retrieval times participate. Supported historical canonicalization remains available. Retrieval recomputes the canonical hash and rejects modified stored content.

A receipt supports reusable content integrity and traceability to evidence available at assessment time. It is not a cryptographic signature, blockchain record, immutable global ledger entry, or proof of a source's correctness or a statement's universal truth.

`state_fingerprint` is `fs_` plus SHA-256 of canonical substantive state: subject type/name/identifiers; facts' normalized value (or value), field, effective date, jurisdiction, provenance type, metadata, and stable source identity; conflict values, source identities, resolution state/reason; and coverage level and field sets. Observation/generation times, receipt ID, evidence IDs, and evidence ordering are excluded.

## Storage and migration

Receipts use the `evidence_receipts` table in `FACTRAIL_CACHE_PATH` (default `/tmp/factrail_cache.db`). Demand events use a separate `evidence_demand` table in the same file. Tables are created additively; the existing cache table and `PRAGMA user_version` are unchanged. Old receipts without `state_fingerprint` receive one in memory; stored JSON and receipt ID are not rewritten.

## Authority, conflicts, and source outcomes

Authority rules are scoped by domain, field, jurisdiction, and optionally time. They rank source classes for that field. A decisive policy retains every assertion and records a resolved conflict with policy ID and reason. Without a decisive rule, the result reports unresolved conflicting sources.

Source outcomes distinguish success, partial responses, source errors/unavailability, source not integrated, disabled lookup, and not required. Unresolved reasons distinguish missing user input, unsupported fields/capabilities, classification needs, and authoritative source unavailability. These represent different causes and should not be collapsed into one generic failure.

French company policies prioritize INSEE/Sirene for current registry status and current legal name over BODACC publication evidence. This precedence does not delete the BODACC record.

## Demand telemetry

Demand telemetry stores normalized operation, capability and field categories, outcome, coverage, unresolved fields, source failure categories, latency, and optional receipt linkage. Unknown capability/field names are hashed; identifiers and free-text scenario content are not stored. Telemetry failures do not fail MCP calls. Run `python3 -m factrail.evidence.demand --days 7` for private aggregates.

The generic public MCP surface is `factrail_capabilities`, `factrail_verify`, `factrail_assess`, and `factrail_get_receipt`. Domain-specific compatibility tools remain callable but new integrations should prefer the generic primitives.
