# FACTRAIL — Evidence infrastructure for AI agents

> Structured, source-backed real-world evidence with provenance, freshness, conflicts, coverage, and reusable receipts.

This Actor is a thin distribution and billing client for the canonical FACTRAIL MCP at **https://mcp.factrail.online/mcp**. It does not implement the evidence resolvers. The source repository and Evidence Core remain at https://github.com/baronsigma/factrail; the direct MCP endpoint remains available.

FACTRAIL is in public beta. Agents should begin with `factrail_capabilities` on the canonical MCP service to discover current support, source state, and limitations; the Apify wrapper does not expose a capability-discovery action. The Actor exposes actions for `factrail_verify`, `factrail_assess`, and `factrail_get_receipt`.

## Current capabilities

- **French company verification (`company_fr`):** supported facts for companies and establishments by SIREN/SIRET, using INSEE / Sirene and BODACC. Field availability depends on records; current registry evidence takes precedence for current status and legal name, while lower-priority evidence remains visible. Supports supplier, vendor, procurement, CRM, marketplace, and KYB-support workflows; FACTRAIL alone is not legally sufficient KYC/KYB.
- **Trade/import assessment (`import`, Beta):** structured import assessment that distinguishes caller input, source evidence, classification candidates, and derived calculations. Results can be partial or provisional.
- **Receipts:** retrieve a previously generated EvidenceEnvelope by receipt ID. Receipt content is content-addressed and integrity-checked; this does not attest universal truth.

No genuine European Commission TARIC snapshot is currently installed. Official TARIC snapshot ingestion infrastructure exists for validated Commission files supplied by an operator. Access2Markets is secondary evidence, and curated tariff and VAT references are provisional. Authoritative EU VAT integration and a complete authoritative landed-cost chain are not available.

## Example actions

### Verify a French company

```json
{
  "action": "verify",
  "input": {
    "subject_type": "company_fr",
    "identifier": "<9-digit SIREN or 14-digit SIRET>",
    "fields": ["status", "legal_name"]
  }
}
```

### Assess an import (Beta)

```json
{
  "action": "assess_import",
  "input": {
    "product": "<product description>",
    "origin_country": "CN",
    "destination_country": "FR",
    "quantity": 1,
    "goods_value": 100,
    "currency": "EUR"
  }
}
```

Optional: `"assessment_date": "YYYY-MM-DD"` sets the TARIC effective/simulation date; availability depends on the installed snapshot. The sample values are illustrative caller inputs. Assessment outputs can be partial/provisional and are not binding customs decisions.

### Retrieve a receipt

```json
{"action":"get_receipt","input":{"receipt_id":"<receipt_id returned by FACTRAIL>"}}
```

The Actor returns `{ "action": "…", "result": <canonical EvidenceEnvelope> }`. Read the receipt ID from a previous FACTRAIL result; no sample result is implied here.

## Pricing

This public Actor uses Pay Per Event. Check the current Apify Pricing tab for live rates and account charges. The Actor should bill only configured qualifying outcomes; partial, unsupported, stale, conflicting, insufficient, and degraded outcomes follow the event rules in the deployed Actor configuration.

## Architecture and privacy

The Actor calls the canonical FACTRAIL MCP service and does not contain source adapters, authority rules, tariff calculations, receipt hashing, or FACTRAIL credentials. It does not intentionally send Apify user IDs, payment IDs, or custom attribution headers to FACTRAIL. Demand telemetry records normalized operation/outcome categories and does not intentionally store raw IP addresses, complete User-Agent strings, full arguments, authorization headers, or entire Evidence envelopes.

## Limits

FACTRAIL does not provide arbitrary natural-language fact checking, general web search, complete global company verification, dedicated sanctions/beneficial ownership/EORI resolution, authoritative installed TARIC data, authoritative EU VAT integration, complete authoritative landed costs, signed receipts, or a blockchain ledger. Use `factrail_capabilities` and the live MCP tool list to confirm deployed support.
