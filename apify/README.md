# FACTRAIL — Evidence for AI Agents

> Evidence infrastructure for AI agents.

This Actor is a thin distribution and payment client for the canonical FACTRAIL MCP at **https://mcp.factrail.online/mcp**. FACTRAIL's [source](https://github.com/baronsigma/factrail) and Evidence Core remain on the canonical service. The direct MCP channel remains available.

Agents can verify supported mutable facts, assess structured imports, and retrieve reusable evidence receipts. Facts identify supporting **sources** (provenance). **Coverage** shows what FACTRAIL could and could not establish. **Conflicts** preserve disagreements. **Freshness** gives observation time context. Each observation has an immutable `receipt_id`; a `state_fingerprint` identifies substantive state across repeat observations.

## Current scope

- **Verification:** French companies and establishments by SIREN or SIRET.
- **Assessment:** structured import scenarios into EU destinations, with France best supported. Trade coverage can be partial when authoritative tariff data or other required information is unavailable.
- **Receipts:** retrieve evidence previously produced by FACTRAIL.

FACTRAIL does not verify arbitrary natural-language claims or provide general search, sanctions, global company coverage, or beneficial ownership checks. This Actor does not expose the experimental `analyze_company` tool.

## Actions

### Verify

```json
{
  "action": "verify",
  "input": {
    "subject_type": "company_fr",
    "identifier": "784671695",
    "fields": ["status", "legal_name"]
  }
}
```

### Assess an import

```json
{
  "action": "assess_import",
  "input": {
    "product": "750ml insulated stainless steel bottle",
    "origin_country": "CN",
    "destination_country": "FR",
    "quantity": 5,
    "goods_value": 1000,
    "currency": "EUR",
    "known_hs_code": "961700"
  }
}
```

### Retrieve a receipt

```json
{
  "action": "get_receipt",
  "input": {"receipt_id": "fr_<64 lowercase hex characters from FACTRAIL>"}
}
```

The Actor returns `{ "action": "…", "result": <canonical EvidenceEnvelope> }`. It keeps all facts, evidence, conflicts, freshness, coverage, receipt and fingerprint fields. Paid results appear in the default dataset; free receipt and partial results appear as `OUTPUT` in the run's default key-value store. Errors also appear in `OUTPUT` with a structured code and no custom event charge.

## Pricing plan

The intended Pay Per Event prices are **$0.005** for one `factrail-verify` event on a supported, sufficiently covered verification and **$0.03** for one `factrail-assess-import` event on a sufficiently evidenced assessment. Receipt retrieval is free. Invalid, unsupported, stale, conflicting, insufficient, or degraded results produce no FACTRAIL custom event. A partial import result caused by a missing authoritative tariff source is free. One run performs one action and emits at most one custom event.

The operator must configure these events in Apify Console, disable both synthetic start and default-dataset-item events, choose **Pay Per Event** without passing platform usage to users, keep **Limited permissions**, and leave Standby disabled. Prices in the repository are a blueprint until accepted in Console. Apify may still incur platform costs for runs that have no FACTRAIL event.

## Architecture and privacy

The Actor calls only three public MCP tools: `factrail_verify`, `factrail_assess`, and `factrail_get_receipt`. It does not contain source adapters, authority rules, calculations, receipt hashing, or FACTRAIL credentials. It does not send Apify user IDs, payment IDs, or custom attribution headers to FACTRAIL. The Canonical service is also usable directly at **https://mcp.factrail.online/mcp**; Apify adds discovery, execution, and billing convenience.

The Actor's bundled schemas are a snapshot of the FACTRAIL 2.1.1 live MCP `tools/list`. If the backend changes its input schemas, update this snapshot before publishing a new Actor build.

## Local development

Install `requirements.txt`, then run tests from the FACTRAIL repository root with `python3 -m pytest -q apify/tests`. Local Apify PPE simulation uses `ACTOR_TEST_PAY_PER_EVENT=true`; its default $1 test price is **not** the configured production price. Real prices and agentic-payment eligibility require Apify Console verification.
