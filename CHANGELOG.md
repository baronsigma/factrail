# Changelog

## 2.4.1 — Public-endpoint fairness, browser clients, discovery metadata

- **Rate limiting:** the client is now identified from `CF-Connecting-IP` (then the first `X-Forwarded-For` entry) only when the direct peer is loopback (the local cloudflared tunnel); otherwise the TCP peer is used. Previously every tunnelled caller shared one bucket.
- Limits are configurable (`FACTRAIL_RATE_LIMIT_PER_MIN`, default 60; `FACTRAIL_RATE_WINDOW_SECONDS`, default 60; `FACTRAIL_RATE_BLOCK_AFTER`, default 30; `FACTRAIL_RATE_BLOCK_SECONDS`, default 300). Known gateway traffic (Smithery, `User-Agent: SmitheryBot` or a Smithery `CF-Worker` header) uses a separate bucket (`FACTRAIL_GATEWAY_RATE_LIMIT_PER_MIN`, default 600, never blocked by default; optional per-end-user keying via `FACTRAIL_GATEWAY_CLIENT_HEADER`).
- Removed the blocking `time.sleep` throttle-wait that stalled the event loop; excess requests now get an immediate HTTP 429 JSON body with `Retry-After`. The repeated-excess block now actually triggers (previously unreachable).
- **Origin:** `FACTRAIL_ALLOWED_ORIGINS="*"` allows browser-based MCP clients from any Origin (CORS enabled, including preflight). The Host allowlist (DNS-rebinding protection) is still enforced. Behaviour is unchanged when the variable is unset.
- **Tool metadata:** every tool has a human `title` (also in `annotations.title`), a leading "Use when …" sentence, and read-only/idempotent/open-world hints. Legacy `verify_french_company`, `assess_import`, and `analyze_company` are marked `[Deprecated]` and remain callable; `FACTRAIL_HIDE_LEGACY_TOOLS=1` hides them from `tools/list`. The stdio server shares the same tool catalog.
- **Server card:** new `GET /.well-known/mcp/server-card.json` static server card (Smithery static-card fields plus SEP-2127 identity/remotes fields), generated from the same tool definitions. `/.well-known/glama.json` is unchanged.

## 2.4.0 — Evidence infrastructure for AI agents (2026-09-28)

- New `factrail_capabilities` tool: discover capability IDs, operations, inputs, fields, sources, dynamic source state, and limitations.
- EvidenceEnvelope schema 1.2 (`schemas/evidence-envelope-1.2.schema.json`): per-fact `support_level` and unresolved-field reasons in coverage metadata. Clients pinning `1.1` must accept `1.2`.
- Beta import assessment: optional `assessment_date`; clearer separation of caller input, source evidence, classification candidates, and derived calculations; derived values inherit dependency quality.
- Access2Markets store as secondary evidence; curated tariff/VAT references stay provisional.
- Official EU TARIC snapshot ingestion foundation (offline doctor, parsing, acceptance report, hashing, normalized SQLite index, atomic activation, last-good/stale handling). No Commission snapshot is installed; official TARIC status is `not_installed`.
- Positioning refresh ("Evidence infrastructure for AI agents") across README, site, `llms.txt`, and listings. Apify Actor accepts evidence schema 1.2.

## 2.3.x — Trust contract (internal milestone, shipped in 2.4.0)

No separate 2.3.x tag or package was published; reconstructed from the 2.4.0 commit and its `test_v23_trust_contract` suite.

- `analyze_company` returns only explicit unavailable values (no fabricated financial/credit scores).
- Schema/model parity checks for EvidenceEnvelope; provisional lineage for trade envelopes.
- Access2Markets "no measures" is explicit; empty parses are not confirmation. TARIC parser errors and stale cache are distinct states; stale tariff cache marks envelopes stale/partial.
- Receipt integrity detects tampering; historical receipt schema hashes remain verifiable.
- Capability registry with MCP filtering; demand telemetry marks unsupported fields separately.

## 2.2.x — Request context and demand semantics (internal milestone, shipped in 2.4.0)

No separate 2.2.x tag or package was published; reconstructed from the 2.4.0 commit and its `test_v22_context_semantics` suite.

- Per-request context (MCP client name/version, HTTP request ID, client family) using context variables isolated across concurrent tasks, propagated to demand telemetry.
- Demand gap categories distinguish unsupported capabilities from source fetch failures; failures follow per-source outcomes.
- Trade import no longer attempts tariff lookup without a classification and never fabricates a rate; source-attempt annotations stay hidden from the legacy trade response shape.

## 2.1.1 — Public release hardening

- Tightened receipt ID validation and public MCP schemas and descriptions.
- Made SQLite cache initialization non-destructive and repeatable; improved readiness and demand telemetry safeguards.
- Updated quickstart, agent documentation, deployment guidance, and release materials.

## 2.1.0 — Trade Evidence, authority policies, demand telemetry

- Added import assessments to the Evidence Core through the existing Trade engine.
- Added state fingerprints, field-scoped authority policies, and private aggregate demand telemetry.
- Added `factrail_assess` while preserving `assess_import`.

## 2.0.0 — Evidence Core

- Introduced EvidenceEnvelope, French company resolution, deterministic persistent receipts, and `factrail_verify` and `factrail_get_receipt`.
- Preserved existing company and Trade tool responses.
