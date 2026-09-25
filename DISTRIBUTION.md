# FACTRAIL listing copy

Use the same core positioning across directories. Keep the umbrella description stable; update the **Available now** sentence only when a new tool is live and visible in MCP `tools/list`.

## Name

FACTRAIL

## Short description

Source-linked, structured facts for AI agents. Live tools verify French companies and assess EU imports, with provenance and explicit uncertainty.

## Longer overview

FACTRAIL connects AI agents to structured, source-linked information through one read-only MCP endpoint. Each data rail addresses a specific real-world question and makes evidence, time context, missing inputs and unavailable source checks clear. Today, agents can verify French companies by SIREN/SIRET using INSEE Sirene and published BODACC events, or request an indicative assessment of an import into the EU. France is the best-supported import destination in the early version. The live MCP tool list defines current coverage as new rails are added.

## Current tool summary

- `verify_french_company` — French company and establishment verification by SIREN/SIRET, with official registry data, published corporate notices and provenance.
- `assess_import` — indicative pre-import assessment for an EU destination, including classification, duty, VAT, compliance, landed-cost and risk considerations. States missing information and unavailable checks. Not a binding customs decision.

## Links

- MCP: https://mcp.factrail.online/mcp
- Docs: https://github.com/baronsigma/factrail
- Website: https://factrail.online/

## Update rule

Do not list proposed financial, procurement, risk, regulatory or other tools as available until they have been deployed and independently observed in `tools/list`. New rails extend the available-tool summary; the core description does not need to change.
