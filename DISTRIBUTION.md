# FACTRAIL listing copy and distribution notes

Canonical positioning: **Evidence infrastructure for AI agents.** FACTRAIL is a public beta that returns structured, source-backed real-world evidence with provenance, freshness, support levels, coverage, conflicts, unresolved dependencies, and reusable content-integrity receipts.

Current strongest capability: French company verification (`company_fr`) using INSEE/Sirene and BODACC. Trade/import assessment (`import`) is Beta and can be partial or provisional. Official TARIC snapshot ingestion infrastructure exists, but no genuine Commission snapshot is currently installed. Access2Markets remains secondary; VAT and curated tariffs are not authoritative integrations.

The hosted deployment may lag repository source. Verify its live MCP `tools/list` and `factrail_capabilities` before changing any registry listing. Do not publish immutable registry versions as part of routine copy edits.

Ready-to-paste material:

- [GitHub](docs/public-listings/github.md)
- [Apify](docs/public-listings/apify.md)
- [Glama](docs/public-listings/glama.md)
- [Smithery](docs/public-listings/smithery.md)

Generic MCP tools to feature: `factrail_capabilities`, `factrail_verify`, `factrail_assess`, `factrail_get_receipt`. Compatibility tools remain callable; prefer the generic tools for new integrations. Keep the product description stable as new capability resolvers are added, and update capability coverage only after the functionality is actually exposed.
