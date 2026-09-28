# Glama listing copy

## Name
FACTRAIL

## Tagline
Evidence infrastructure for AI agents.

## Short description
Structured, source-backed real-world evidence with provenance, freshness, conflicts, coverage, and reusable receipts.

## Long description
FACTRAIL is a public beta MCP service that gives agents structured Evidence instead of naked answers. Start with `factrail_capabilities` to discover current operations, inputs, sources, limitations, and dynamic source state. Use `factrail_verify` for supported French company facts, `factrail_assess` for beta import assessments, and `factrail_get_receipt` to retrieve integrity-checked Evidence.

French company verification uses INSEE / Sirene and BODACC; record availability varies, and lower-priority publication evidence remains visible. Trade/import assessment separates caller input, source evidence, classification candidates, and calculations; results may be partial or provisional. Official TARIC snapshot ingestion infrastructure exists, but no genuine Commission snapshot is installed. Access2Markets is secondary and curated tariff/VAT references are provisional.

Receipts are content-addressed and checked for content integrity. They are not digital signatures, blockchain records, or proof of absolute truth. Unknown is better than invented: stale data, source failures, unresolved dependencies, and conflicts remain explicit.

## Tools
- `factrail_capabilities` — discover support and limitations.
- `factrail_verify` — structured verification; currently `company_fr`.
- `factrail_assess` — structured assessment; currently beta `import`.
- `factrail_get_receipt` — retrieve a prior EvidenceEnvelope by receipt ID.

Compatibility tools remain available: `verify_french_company`, `assess_import`, and experimental `analyze_company`. New integrations should prefer the generic primitives.

## Use cases
Supplier verification, vendor onboarding, procurement agents, B2B research, CRM enrichment, marketplace onboarding, business-data workflows, and company/KYB-support workflows. This service alone is not legally sufficient KYC/KYB compliance.

## Limitations
No arbitrary natural-language fact checking, general web search, complete global company coverage, dedicated beneficial ownership/sanctions/EORI resolver, authoritative installed EU TARIC data, authoritative EU VAT integration, complete authoritative landed cost, or signed/blockchain receipts. Check `factrail_capabilities` for deployed coverage.

## Endpoint
https://mcp.factrail.online/mcp
