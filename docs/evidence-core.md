# Evidence Core in FACTRAIL v2.2.0

FACTRAIL's Evidence Contract is independent of the product version. Package v2.2.0 emits contract `1.1` envelopes. Version `1.0` receipts remain readable.

## Contract

`EvidenceEnvelope` contains `schema_version`, `status`, `subject`, `facts`, `evidence`, `conflicts`, `coverage`, `freshness`, `receipt_id`, `state_fingerprint`, and `generated_at`. Status is a strict enum: `supported`, `contradicted`, `insufficient_evidence`, `stale`, or `conflicting_sources`. Every fact cites at least one evidence ID that exists in the envelope. A fact's `provenance_type` distinguishes sourced, normalized, inferred, estimated, caller-input, and derived values. These types are descriptive; they are not confidence percentages.

Company verification uses INSEE Sirene and BODACC. BODACC removal notices are historical records and do not supersede current registry status. Trade assessment calls the existing `assess_import` engine and maps its output to source, input, and calculation records. The engine attempts an Access2Markets lookup when it has a classified HS code; successful but incomplete responses, source errors, and curated fallback evidence are separately identified.

## IDs

`receipt_id` is `fr_` plus SHA-256 of canonical JSON (`sort_keys=True`, UTF-8, compact separators, no NaN). All envelope fields participate except `receipt_id`, `state_fingerprint`, top-level `generated_at`, and `freshness.generated_at`. Retrieval times participate. For a `1.0` envelope, the added fact `provenance_type` field is also excluded so its old hash remains unchanged. The first complete envelope stored for an ID is retained.

`state_fingerprint` is `fs_` plus SHA-256 of canonical substantive state: subject type/name/identifiers; each fact's normalized value when present, otherwise its value, plus field, effective date, jurisdiction, provenance type, metadata, and stable source identity (type, authority class, publisher, URL); conflict values, source identities, resolution status and reason; and coverage level plus requested, resolved, and unresolved field sets. Facts, conflict sides, conflicts, source references, and field lists are sorted. Observation and generation times, receipt ID, evidence IDs, and evidence order are excluded.

## Storage and migration

Receipts use the `evidence_receipts` table in `FACTRAIL_CACHE_PATH` (default `/tmp/factrail_cache.db`). Demand events use a separate `evidence_demand` table in the same file. Both are created with `CREATE TABLE IF NOT EXISTS`; the existing cache table and `PRAGMA user_version` are unchanged. On read, an old receipt without `state_fingerprint` receives one in memory. The stored JSON and receipt ID are not rewritten.

## Authority and demand

Authority rules are scoped to domain, field, jurisdiction, and optionally time. They rank named source classes for that field. A decisive rule retains every assertion and creates a resolved conflict with policy ID and reason. Without one, the conflict remains unresolved and status is `conflicting_sources`.

Demand telemetry stores normalized operation, capability and field categories, outcome, coverage, unresolved fields, unsupported flag, source failure categories, latency, and optional cache status. Unknown capability and field names are hashed; identifiers and free-text scenario content are never stored. Telemetry failures are logged and do not fail MCP calls. Run `python3 -m factrail.evidence.demand --days 7` for private aggregate output.

## FACTRAIL v2.1 reliability and demand attribution

Evidence demand SQLite records now include `origin_class`, normalized `client_family`, `server_version`, `request_id`, `receipt_id`, and field-level unresolved reason codes. Existing rows migrate additively and read as `unknown`; no raw IP, User-Agent, tool arguments, or evidence envelope is copied into demand telemetry. The HTTP telemetry retains its rotating daily HMAC client identifier for coarse uniqueness and never stores the IP input. Explicit `X-Factrail-Test-Origin: internal_test|live_test|manual` markers classify controlled probes. MCP client-name metadata is normalized to a small family vocabulary. Unknown remains unknown rather than being inferred as external from absence of test markers.

`python3 -m factrail.evidence.demand --days N` defaults to explicitly attributed `external` calls. Use `--origin internal_test`, `--origin live_test`, `--origin manual`, `--origin inspector`, `--origin unknown`, or `--all-origins` to inspect other traffic. Library `DemandStore.report()` retains its historical all-row default for compatibility. Old unattributed demand events therefore remain visible in all-origin reports and are excluded from default CLI external demand.

New demand rows carry source-semantics version 2. Only actual failed/unavailable attempted outcomes populate `source_failures`; `source_not_integrated`, `lookup_disabled`, `partial`, `success`, and `not_required` are shown in `source_outcomes` and actionable gaps as applicable. Historical v2.1 source labels remain intact but are reported as `legacy_source_failure_labels` because their attempt status cannot be reconstructed. `actionable_gaps` separates missing caller input, unsupported facts/capabilities, and source integration or retrieval gaps using raw counts.

The installed MCP SDK carries the HTTP request and the negotiated `session.client_params.client_info` on its per-request handler context when client info is supplied. FACTRAIL normalizes allowlisted client identities and versions, binds them in a context variable for the duration of the tool call, and attaches them to request-local Starlette state for the HTTP middleware. Modern calls without client info and unrecognized client names remain `unknown`. Explicit test markers and `FACTRAIL_TEST_MODE=1` take precedence over client metadata.

### Company source authority policies

The inspected `InseeAdapter.lookup_with_bodacc` uses INSEE/Sirene for current registry facts and attaches BODACC as official publication evidence. Its BODACC notices are historical and intentionally do not override INSEE's current status. Accordingly, the registry defines two field-scoped France policies:

- `company_fr.status`: primary official registry before official publication for current administrative status.
- `company_fr.legal_name`: primary official registry before official publication for the current registered name.

The generic authority resolver preserves both sides in conflicts. It resolves only when the best-ranked evidence uniquely outranks the next assertion; tied or unranked evidence remains conflicting. Company envelopes expose the policy definitions in coverage metadata. Other fields have no precedence policy.

### Import unresolved reason and TARIC diagnosis

Each unresolved import field has a reason code such as `missing_user_input`, `unsupported_fact`, `source_unavailable`, `source_error`, `source_not_integrated`, `lookup_disabled`, `classification_required`, or `not_applicable`. The existing `TaricStore` attempts EU Access2Markets point lookups when an HS code is available. A failed request is `source_error`; an empty parsed response is `partial`; a path intentionally not run is `lookup_disabled` or `not_required`. Curated heading-level references remain explicitly limited and do not become authoritative tariff facts.

Demand analytics store only normalized requested/unresolved field categories, outcomes, source failure categories, and a receipt ID. Receipt IDs link the request event to the already stored envelope without duplicating that envelope in analytics.

The public Evidence Core surface is `factrail_verify` (verify a real-world fact), `factrail_assess` (evaluate a structured real-world situation), and `factrail_get_receipt` (retrieve reusable evidence). Trade import assessment uses the canonical EvidenceEnvelope and ordinary receipt storage. Legacy/domain-specific tools remain supported with their original response shapes.
