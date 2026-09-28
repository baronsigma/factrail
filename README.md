# FACTRAIL

**Evidence infrastructure for AI agents.**

FACTRAIL gives agents structured, source-backed real-world evidence with provenance, freshness, conflicts, support levels, coverage, unresolved dependencies, and reusable receipts. It is a public beta: capabilities are defined and inspectable, and unsupported scope or failed sources are represented explicitly.

FACTRAIL is a Python [Model Context Protocol](https://modelcontextprotocol.io/) server. This repository contains package version 2.4.0, the Evidence Contract, tests, operator tooling, and the static site. Hosted deployments may run a different release; inspect the live `tools/list` and `factrail_capabilities` results before relying on deployed capabilities.

## Connect via MCP

Endpoint: **`https://mcp.factrail.online/mcp`** (Streamable HTTP)

```json
{
  "mcpServers": {
    "factrail": {
      "url": "https://mcp.factrail.online/mcp"
    }
  }
}
```

Client configuration varies. Website: [factrail.online](https://factrail.online/) · Health: [healthz](https://mcp.factrail.online/healthz).

## Why FACTRAIL

Agents should not have to choose between blindly trusting model output and rebuilding source verification from scratch. FACTRAIL provides an evidence layer between agents and real-world data sources. Instead of returning only an answer, it can return what was established, where it came from, how current it is, what remains unresolved, and a receipt another agent can inspect later.

The principle is simple: **unknown is better than invented.** Caller input is not automatically verified. Derived values inherit the quality of their dependencies. Conflicts, stale data, and source failures remain visible. A capability exposes its limits programmatically.

## Core MCP tools

New integrations should use the generic primitives:

| Tool | Purpose |
| --- | --- |
| `factrail_capabilities` | Discover available capability IDs, operations, versions, inputs, fields, sources, dynamic source state, and limitations. Call this first. |
| `factrail_verify` | Verify structured facts for a supported subject. Currently supports `company_fr`. |
| `factrail_assess` | Assess a structured real-world situation. Currently supports `import` (**Beta**). |
| `factrail_get_receipt` | Retrieve a prior EvidenceEnvelope by receipt ID and check content integrity. |

Compatibility tools remain available: `verify_french_company`, `assess_import`, and experimental/compatibility-only `analyze_company`. New clients should prefer the generic tools. `analyze_company` does not synthesize unavailable finance, credit, rating, risk, recommendation, executive-count, or confidence values; unavailable fields remain null/unavailable.

## Available today: French company verification

The `company_fr` capability is FACTRAIL's strongest current resolver. It verifies supported French company and establishment information by SIREN or SIRET using INSEE / Sirene and BODACC records. Depending on source records, fields can include identifiers, legal and trade names, status, legal form, NAF/activity code, head office, creation/cessation dates, diffusion status, and BODACC events. No field is guaranteed to exist for every entity.

For current company status and current legal name, INSEE/Sirene current registry data takes precedence over BODACC publication evidence. Lower-priority evidence is retained rather than silently discarded. This can support supplier verification, vendor onboarding, procurement, CRM enrichment, marketplace onboarding, and company/KYB-support workflows. FACTRAIL alone is not a legally sufficient KYC/KYB compliance system.

Example input to `factrail_verify` (replace the placeholder with an identifier you are authorized to check):

```json
{
  "subject_type": "company_fr",
  "identifier": "<9-digit SIREN or 14-digit SIRET>",
  "fields": ["status", "legal_name", "head_office"]
}
```

The actual source response depends on the requested identifier, source availability, and record coverage. FACTRAIL does not fabricate a sample result here.

## Beta: trade and import assessment

`factrail_assess` currently supports `assessment_type="import"`. It accepts structured concepts such as origin, destination, product description, classification or known HS/customs code, goods value, quantity, customs/VAT references, compliance inputs, and freight/insurance values. The result separates caller input, source evidence, classification candidates, and deterministic calculations. Coverage can be partial or provisional.

There is no authoritative EU Member-State VAT integration or complete authoritative landed-cost chain. Curated VAT and tariff values stay marked curated/provisional; derived calculations inherit dependency quality (`derived_provisional` when their inputs are provisional). Trade assessment is not a binding customs ruling and classification candidates are not definitive customs classification.

### Official EU TARIC status

FACTRAIL has infrastructure for validated official European Commission TARIC snapshot ingestion: offline package doctor, strict/compatible parsing, acceptance reporting, hashes/fingerprints, semantic checks, normalized SQLite index, atomic activation, and last-good/stale handling. **No genuine Commission TARIC snapshot is currently installed** (`not_installed`). FACTRAIL does not currently serve authoritative live TARIC tariff coverage.

Access2Markets is secondary/supporting evidence, never authoritative TARIC. Curated FACTRAIL tariff references are provisional. No stable official automatic-download endpoint was confirmed, so automatic fetch is unavailable at this time. Operator-supplied official files can be diagnosed and ingested offline; monthly snapshot ingestion is supported, while daily-delta application is deferred.

For operator procedures see [TARIC snapshot acceptance and operations](docs/taric-v2.4b-operator.md) and the [distribution discovery notes](docs/taric-v2.4a-discovery.md). Detailed tariff calculation is outside the current TARIC snapshot foundation.

## Evidence Contract

The canonical `EvidenceEnvelope` includes `schema_version`, status, subject, facts, evidence sources, conflicts, coverage, freshness, `receipt_id`, and `generated_at`. The current schema is [EvidenceEnvelope 1.2](schemas/evidence-envelope-1.2.schema.json); it is independent of the package version.

- **Status** can be `supported`, `contradicted`, `insufficient_evidence`, `stale`, or `conflicting_sources`; results are not reduced to a boolean.
- **Support levels** describe evidence quality: `authoritative`, `supported`, `derived_supported`, `derived_provisional`, `curated`, and `caller_input`. Caller input is not externally verified.
- **Coverage** distinguishes requested, resolved, and unresolved fields. Missing input, unsupported fields, and unavailable sources are different states.
- **Freshness** records observation times and stale status.
- **Conflicts** preserve competing source assertions; field-specific authority policy may resolve a conflict without deleting its lower-priority evidence.
- **Source outcomes** distinguish success, partial response, source error/unavailable, source not integrated, disabled lookup, and not required.

A deterministic calculation does not become authoritative merely because its arithmetic is deterministic. Authoritative inputs may support a `derived_supported` result; provisional inputs produce `derived_provisional` results.

## Receipts

A receipt ID (`fr_…`) is content-addressed using deterministic canonical hashing. On retrieval, FACTRAIL recomputes the hash and rejects altered content with an integrity error. Supported historical canonicalization remains available. Receipts make an evidence observation reusable and traceable; they provide **content integrity**, not a digital signature, blockchain record, immutable global ledger entry, or proof that a statement is universally true. The `state_fingerprint` identifies substantive state across observations. See [Evidence Core design](docs/evidence-core.md).

## Sources and trust principles

FACTRAIL's source hierarchy is explicit: official records may be authoritative for defined fields, secondary sources remain secondary, curated references remain curated, and caller input remains input. Source identity, retrieval time, content hashes where available, support level, and source status accompany evidence. Historical publication evidence does not automatically determine current state.

FACTRAIL follows these principles:

1. Never manufacture evidence; unknown is better than invented.
2. Caller input is not automatically verified.
3. Derived values inherit the support quality of their dependencies.
4. Conflicting sources remain visible.
5. Source failures and stale evidence are explicit.
6. Secondary evidence is not silently promoted to authoritative.
7. Receipts protect content integrity, not philosophical truth.
8. Capability limits are exposed programmatically.

## Quick start: discover, verify, retrieve

Use `factrail_capabilities` first with `{}`. The live result identifies currently supported capability IDs and dynamic source state. Then a company verification call uses:

```json
{
  "subject_type": "company_fr",
  "identifier": "<SIREN or SIRET>",
  "fields": ["status", "legal_name"]
}
```

To retrieve the result later, pass its actual `receipt_id`:

```json
{"receipt_id":"<receipt_id returned by FACTRAIL>"}
```

MCP tool calls wrap these argument objects in their normal client protocol. See [request examples](docs/examples.md) for generic verify, receipt, and import inputs. Examples use placeholders and do not assert fabricated output.

## Run locally

Requires Python 3.11 or newer.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
# Set INSEE_API_KEY in .env for live French company lookups.
python3 -m factrail.mcp_http_server
```

HTTP defaults to port 8765. Use `FACTRAIL_HOST` and `FACTRAIL_PORT` to change the bind address and port. Local MCP endpoint: `POST http://localhost:8765/mcp`; health endpoint: `/healthz`. For stdio clients, run `python3 -m factrail.mcp_server`.

Get an INSEE key through the [INSEE API portal](https://portail-api.insee.fr/). Default SQLite cache is `/tmp/factrail_cache.db`; set `FACTRAIL_CACHE_PATH` to a persistent path for durable receipts. The server includes per-client rate limiting and stale cache fallback for company lookups.

## Current limitations

FACTRAIL does not currently provide arbitrary natural-language fact checking, general web search or URL fetching, news verification, broad global company verification, dedicated beneficial ownership/sanctions/EORI resolvers, authoritative installed EU TARIC data, authoritative EU VAT integration, complete authoritative landed-cost results, arbitrary regulatory verification, signed receipts, blockchain receipts, global immutable evidence ledgers, continuous evidence watches, or general-purpose LLM reasoning over unknown claims.

## Deployment and development

For operators, see the [deployment checklist](docs/deployment-checklist.md), [TARIC acceptance guide](docs/taric-v2.4b-operator.md), and [distribution notes](DISTRIBUTION.md). Release history: [CHANGELOG](CHANGELOG.md). The static site is in `site/` and needs no build step.

```bash
python3 -m pytest -q
python3 -m compileall -q factrail tests
python3 -m factrail.evidence.demand --days 7
```

The offline suite uses mocks and fixtures; live tests follow the project's `--live` convention and require external access. Demand telemetry stores normalized categories and linkage, not raw IP addresses, full User-Agent strings, request arguments, authorization headers, or full Evidence envelopes. It helps identify requested capabilities and evidence gaps.

The [Apify Actor wrapper](apify/README.md) is a thin distribution/payment channel for the canonical MCP service; it does not change FACTRAIL's source adapters or evidence behavior.

## Public discovery surfaces

- [Website](https://factrail.online/)
- [Agent guidance](site/llms.txt)
- [Apify listing copy](docs/public-listings/apify.md)
- [Glama listing copy](docs/public-listings/glama.md)
- [Smithery listing copy](docs/public-listings/smithery.md)
- [GitHub metadata](docs/public-listings/github.md)

## License

Code and documentation are licensed under [Apache License 2.0](LICENSE). Source data remains subject to publisher terms, including INSEE and BODACC open-data licenses.
