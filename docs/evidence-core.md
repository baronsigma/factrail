# Evidence Core v2.1

FACTRAIL's Evidence Contract is independent of the product version. Package v2.1 emits contract `1.1` envelopes. Version `1.0` receipts remain readable.

## Contract

`EvidenceEnvelope` contains `schema_version`, `status`, `subject`, `facts`, `evidence`, `conflicts`, `coverage`, `freshness`, `receipt_id`, `state_fingerprint`, and `generated_at`. Status is a strict enum: `supported`, `contradicted`, `insufficient_evidence`, `stale`, or `conflicting_sources`. Every fact cites at least one evidence ID that exists in the envelope. A fact's `provenance_type` distinguishes sourced, normalized, inferred, estimated, caller-input, and derived values. These types are descriptive; they are not confidence percentages.

Company verification uses INSEE Sirene and BODACC. BODACC removal notices are historical records and do not supersede current registry status. Trade assessment calls the existing `assess_import` engine and maps its output to source, input, and calculation records. When authoritative tariff data is unavailable and the engine uses a curated fallback, the unavailable source is visible and coverage is partial.

## IDs

`receipt_id` is `fr_` plus SHA-256 of canonical JSON (`sort_keys=True`, UTF-8, compact separators, no NaN). All envelope fields participate except `receipt_id`, `state_fingerprint`, top-level `generated_at`, and `freshness.generated_at`. Retrieval times participate. For a `1.0` envelope, the added fact `provenance_type` field is also excluded so its old hash remains unchanged. The first complete envelope stored for an ID is retained.

`state_fingerprint` is `fs_` plus SHA-256 of canonical substantive state: subject type/name/identifiers; each fact's normalized value when present, otherwise its value, plus field, effective date, jurisdiction, provenance type, metadata, and stable source identity (type, authority class, publisher, URL); conflict values, source identities, resolution status and reason; and coverage level plus requested, resolved, and unresolved field sets. Facts, conflict sides, conflicts, source references, and field lists are sorted. Observation and generation times, receipt ID, evidence IDs, and evidence order are excluded.

## Storage and migration

Receipts use the `evidence_receipts` table in `FACTRAIL_CACHE_PATH` (default `/tmp/factrail_cache.db`). Demand events use a separate `evidence_demand` table in the same file. Both are created with `CREATE TABLE IF NOT EXISTS`; the existing cache table and `PRAGMA user_version` are unchanged. On read, an old receipt without `state_fingerprint` receives one in memory. The stored JSON and receipt ID are not rewritten.

## Authority and demand

Authority rules are scoped to domain, field, jurisdiction, and optionally time. They rank named source classes for that field. A decisive rule retains every assertion and creates a resolved conflict with policy ID and reason. Without one, the conflict remains unresolved and status is `conflicting_sources`.

Demand telemetry stores normalized operation, capability and field categories, outcome, coverage, unresolved fields, unsupported flag, source failure categories, latency, and optional cache status. Unknown capability and field names are hashed; identifiers and free-text scenario content are never stored. Telemetry failures are logged and do not fail MCP calls. Run `python3 -m factrail.evidence.demand --days 7` for private aggregate output.
