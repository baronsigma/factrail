# Factrail

**Verified facts for agents.**

Factrail is a public, read-only MCP service for verifying French companies and establishments by **SIREN** or **SIRET** using official **INSEE Sirene** data and published **BODACC** corporate events.

> This repository is the public discovery/documentation surface for Factrail. The production MCP service is hosted remotely.

## Connect

**Streamable HTTP MCP endpoint**

```text
https://mcp.factrail.online/mcp
```

**Tool**

```text
verify_french_company
```

The tool accepts:

- a 9-digit **SIREN**, or
- a 14-digit **SIRET**

and returns structured registry data with provenance.

## What Factrail returns

When available, Factrail returns:

- legal identity
- administrative status
- registered establishment address
- creation / cessation dates
- NAF / APE activity
- workforce band
- INSEE source metadata
- published BODACC corporate events
- source URLs and retrieval timestamps

Factrail distinguishes missing data from negative findings and does not infer legal or financial reliability from registry facts.

## Example

Ask an MCP-capable agent:

> Check whether French company SIREN 356000000 is currently active and summarize notable published corporate events.

An agent can discover `verify_french_company`, call Factrail, and use the returned INSEE + BODACC data to answer.

## MCP client configuration

For clients that accept a remote Streamable HTTP MCP URL, use:

```text
https://mcp.factrail.online/mcp
```

Example configuration:

```json
{
  "mcpServers": {
    "factrail": {
      "url": "https://mcp.factrail.online/mcp"
    }
  }
}
```

Client configuration formats vary, so use the remote MCP / Streamable HTTP option supported by your client.

## Sources

### INSEE Sirene

Official French business-register data used for company and establishment identity, status and related registry fields.

### BODACC

Published French corporate notices used for event history such as modifications, accounts filings, sales/transfers and other published notices.

## Provenance

Factrail is designed for agent workflows where the difference between a fact and an inference matters.

Responses include source metadata and retrieval timestamps where available. Historical BODACC notices are reported as published events; their presence does not by itself imply a current legal or financial condition.

## Public links

- Website: https://factrail.online
- MCP endpoint: https://mcp.factrail.online/mcp
- Health: https://mcp.factrail.online/healthz
- Official MCP Registry: https://registry.modelcontextprotocol.io/?q=io.github.baronsigma%2Ffactrail
- Glama: https://glama.ai/mcp/connectors/io.github.baronsigma/factrail
- Smithery: https://smithery.ai/servers/baronsigma/factrail

## Registry identity

```text
io.github.baronsigma/factrail
```

Current public MCP endpoint:

```text
https://mcp.factrail.online/mcp
```

## Privacy

Factrail's usage telemetry is designed to measure real MCP tool calls without storing SIREN/SIRET inputs, raw request payloads, returned company data, raw IP addresses, authorization headers or cookies.

## Status

Factrail is live as a public MCP service. The current scope is intentionally narrow: one deterministic company-verification tool backed by authoritative public sources.

---

**Factrail — Verified facts for agents.**
