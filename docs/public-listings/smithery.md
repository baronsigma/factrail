# Smithery listing copy

## Name
FACTRAIL

## Tagline
Evidence infrastructure for AI agents.

## Description
FACTRAIL gives agents structured, source-backed real-world evidence with provenance, freshness, support levels, coverage, conflicts, and reusable receipts. Call `factrail_capabilities` to discover actual support, then use `factrail_verify`, `factrail_assess`, and `factrail_get_receipt`.

The strongest current capability verifies French company facts using INSEE / Sirene and BODACC. Trade/import assessment is Beta and may be partial or provisional. Official TARIC snapshot ingestion is supported, but no genuine Commission snapshot is installed. Access2Markets is secondary; curated tariff/VAT references remain provisional. Receipts are integrity-checked content-addressed records, not signatures or blockchain attestations.

## Tools
- `factrail_capabilities`
- `factrail_verify` (`company_fr`)
- `factrail_assess` (Beta `import`)
- `factrail_get_receipt`

Legacy compatibility tools remain callable. Prefer the generic tools for new integrations.

## Limitations
No arbitrary fact checking, general web search, complete global company verification, authoritative installed TARIC, authoritative EU VAT integration, complete authoritative landed-cost calculation, dedicated sanctions/beneficial ownership resolver, or signed/blockchain receipts. FACTRAIL is public beta; capability metadata is authoritative for current scope.

## MCP endpoint
https://mcp.factrail.online/mcp

## Links
- https://factrail.online/
- https://github.com/baronsigma/factrail
- https://mcp.factrail.online/healthz
