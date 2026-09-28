# Apify listing copy

## Title
FACTRAIL — Evidence infrastructure for AI agents

## One-line description
Structured, source-backed company verification and beta import assessment with provenance and reusable receipts.

## Short description
FACTRAIL gives agents structured Evidence instead of naked answers. Discover supported capabilities, verify French company facts, assess import scenarios in public beta, and retrieve integrity-checkable Evidence receipts through MCP.

## Full description
FACTRAIL is evidence infrastructure for AI agents. The canonical MCP service exposes capability discovery, structured verification and assessment, and receipt retrieval; the Apify wrapper currently provides verify, assess, and receipt actions. Call `factrail_capabilities` directly on the MCP service to inspect current coverage. Evidence carries source identity, support level, freshness, coverage, conflicts, and unresolved dependencies.

Available today, the `company_fr` capability verifies supported French company and establishment facts using INSEE / Sirene and BODACC records. Field availability varies by source record; current registry facts take precedence for current status and legal name while lower-priority evidence remains visible. This can support supplier verification, vendor onboarding, procurement, CRM enrichment, marketplace onboarding, and agentic KYB-support workflows. FACTRAIL alone is not a legally sufficient KYC/KYB system.

The `import` capability is Beta. It assesses structured import scenarios and distinguishes caller input, source evidence, classification candidates, and derived calculations. Results can be partial or provisional. FACTRAIL has official TARIC snapshot ingestion infrastructure, but no genuine Commission snapshot is currently installed. Access2Markets is secondary; curated tariff and VAT references remain curated/provisional. There is no authoritative EU VAT integration or complete authoritative landed-cost chain.

Unknown is better than invented: missing inputs, unsupported scope, source failures, stale data, and conflicts are represented explicitly. Receipts are content-addressed and integrity-checked; they are not signatures, blockchain records, or proof of universal truth.

## Features
- Capability discovery via the canonical MCP endpoint (`factrail_capabilities`; call directly, outside the Apify Actor actions)
- French company verification by SIREN/SIRET
- Structured EvidenceEnvelope with provenance and support levels
- Freshness, coverage, unresolved fields, and conflict reporting
- Beta structured trade/import assessments
- Content-addressed, integrity-checked receipt retrieval
- Explicit source hierarchy and failure semantics

## Limitations
No arbitrary fact checking, general web search, broad global company coverage, dedicated beneficial ownership/sanctions/EORI resolver, authoritative installed TARIC data, authoritative EU VAT integration, complete authoritative landed cost, legally sufficient KYC/KYB compliance, signed receipts, or blockchain ledger. Inspect `factrail_capabilities` for current source state.

## Category and tags
Categories: AI, Business, Developer tools
Tags: MCP, AI agents, evidence, provenance, verification, French company data, trade data, receipts

## Endpoint and links
- MCP: https://mcp.factrail.online/mcp
- Website: https://factrail.online/
- GitHub: https://github.com/baronsigma/factrail
- Health: https://mcp.factrail.online/healthz

## Example use case
A procurement agent calls `factrail_capabilities`, then `factrail_verify` with `subject_type="company_fr"` and a supplier SIREN. It reviews INSEE/Sirene and BODACC provenance, freshness, coverage and any conflicts, then stores the returned receipt ID for later retrieval.
