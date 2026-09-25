# FACTRAIL

**Source-linked facts for AI agents.**

FACTRAIL is a public, read-only MCP service that turns external data into structured answers an agent can inspect and cite. It is designed to grow across domains: each rail has a specific question, explicit sources, provenance, and clear treatment of missing or unavailable evidence.

> This repository documents the public service and contains the website source. The production MCP implementation is hosted separately.

## Connect

Use the remote **Streamable HTTP** MCP endpoint:

```text
https://mcp.factrail.online/mcp
```

For clients using an `mcpServers` configuration:

```json
{
  "mcpServers": {
    "factrail": {
      "url": "https://mcp.factrail.online/mcp"
    }
  }
}
```

Client configuration formats vary. The server's `tools/list` response is the authoritative list of currently available capabilities.

## Available now

| Rail | Tool | What it does |
| --- | --- | --- |
| Company facts | `verify_french_company` | Verifies a French company or establishment by nine-digit SIREN or 14-digit SIRET. Returns normalized INSEE Sirene registry facts and published BODACC events with source context. |
| Trade | `assess_import` | Produces an indicative pre-import assessment for goods entering an EU destination from any origin. France is the best-supported destination in this early version. It organizes classification, duty, VAT, compliance, landed-cost and risk considerations, asks for missing information, and marks unavailable source checks explicitly. |

**Try asking an MCP-capable agent:**

- “Verify French company SIREN 356000000 and summarize its published corporate events.”
- “Assess importing 100 insulated stainless-steel bottles from China into France for a goods value of EUR 2,000. What information is still needed?”

`assess_import` is a decision aid, not a binding tariff ruling or a customs filing service. In the current version, some trade source adapters are unavailable; a modeled or indicative result must not be mistaken for a live official lookup. Review classification, rates, regulatory requirements and costs against applicable official sources before acting.

## The FACTRAIL approach

An agent should be able to distinguish a sourced observation from an estimate, an inference, or an unavailable check. Rails aim to provide:

1. **A specific answer** in a machine-readable shape.
2. **Evidence and provenance** identifying the source where available.
3. **Time context** such as publication or retrieval timestamps where available.
4. **Explicit uncertainty** for missing inputs, unavailable sources and non-binding assessments.

This is the common product contract as additional domains are added. Coverage, source availability and fields differ by tool; inspect each tool's description and output for its exact limits. Published notices are historical events and do not, by themselves, establish a company's current financial or legal condition.

## Growing the rails

Company facts and trade are the current rails. Further domains may include corporate events, financials, procurement, supplier due diligence and other real-world data. These are directions for development, **not currently advertised MCP tools**. New capabilities will be added to the table above when they are live.

## Links

- Website: https://factrail.online/
- MCP endpoint: https://mcp.factrail.online/mcp
- Health: https://mcp.factrail.online/healthz
- Official MCP Registry: https://registry.modelcontextprotocol.io/?q=io.github.baronsigma%2Ffactrail
- Glama: https://glama.ai/mcp/connectors/io.github.baronsigma/factrail
- Smithery: https://smithery.ai/servers/baronsigma/factrail

Registry identity: `io.github.baronsigma/factrail`.

## Privacy

FACTRAIL's usage telemetry is designed to count actual MCP tool calls without retaining SIREN/SIRET inputs, raw request payloads, returned company data, raw IP addresses, authorization headers or cookies.

## Website source

The static public site lives in [`site/`](site/). Its evergreen overview and current-tool table can be maintained independently: add a tool to the table only after it is deployed and observed in the MCP `tools/list` response.
