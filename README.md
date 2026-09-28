# FACTRAIL

**FACTRAIL is evidence infrastructure for AI agents.**

Agents routinely need facts that can change: a company's registration status, a tariff rate, or the information needed to assess an import. FACTRAIL gives them structured answers with source references, coverage, freshness, and receipts they can retrieve later.

FACTRAIL is a Python [Model Context Protocol](https://modelcontextprotocol.io/) server. This repository contains the v2.4.0 source, tests, and the public site. The hosted service may run a different release; check its live `tools/list` response before relying on a tool being deployed.

## Connect to the hosted service

MCP endpoint: **`https://mcp.factrail.online/mcp`** (Streamable HTTP)

```json
{
  "mcpServers": {
    "factrail": {
      "url": "https://mcp.factrail.online/mcp"
    }
  }
}
```

Client configuration varies. The endpoint's `tools/list` response is the source of truth for deployed capabilities. [Website](https://factrail.online/) · [Health endpoint](https://mcp.factrail.online/healthz)

### MCP quickstart

Call `factrail_verify` to check a French company:

```json
{"subject_type":"company_fr","identifier":"784671695","fields":["status","legal_name"]}
```

Call `factrail_assess` to evaluate an import scenario:

```json
{"assessment_type":"import","parameters":{"product":"750ml insulated stainless steel bottle","origin_country":"CN","destination_country":"FR","quantity":5,"goods_value":1000,"currency":"EUR","known_hs_code":"961700"}}
```

Use the returned `receipt_id` with `factrail_get_receipt`:

```json
{"receipt_id":"fr_<64 lowercase hex characters from a FACTRAIL response>"}
```

## What the v2.4 source provides

| Tool | Purpose |
| --- | --- |
| `factrail_verify` | Verify structured French company fields by SIREN or SIRET and return an EvidenceEnvelope. |
| `factrail_assess` | Assess a structured EU import scenario with the existing Trade engine and return an EvidenceEnvelope. |
| `factrail_get_receipt` | Retrieve a previously stored EvidenceEnvelope by receipt ID. |
| `factrail_capabilities` | Discover registered operations, inputs, fields, sources, and limitations. |
| `verify_french_company` | Existing INSEE Sirene and BODACC company response. |
| `assess_import` | Existing classification, duty, VAT, compliance, and landed-cost response. |
| `analyze_company` | Deprecated compatibility-only interface. Financial and credit sources are unavailable; values are null. |

`factrail_capabilities` discovers what FACTRAIL currently supports. `factrail_verify` verifies structured real-world facts. `factrail_assess` evaluates a structured real-world situation. `factrail_get_receipt` retrieves reusable evidence. Legacy tools remain callable.

### Verify a French company

```json
{
  "subject_type": "company_fr",
  "identifier": "784671695",
  "fields": ["status", "legal_name", "head_office"]
}
```

Call `factrail_verify` with that input. The company resolver uses the existing INSEE Sirene and BODACC pipeline. Historical notices remain historical; they do not silently change the current INSEE status.

### Assess an import

```json
{
  "assessment_type": "import",
  "parameters": {
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

Call `factrail_assess` with that input. `parameters` accepts the same fields as [`assess_import`](factrail/trade/models.py). The assessment identifies caller inputs, source data, inferred classifications, and derived calculations separately. A curated tariff fallback is marked as such; it is not a live authoritative tariff lookup. Classification and cost estimates are indicative and require review before a customs filing.

Tariff `source_outcome` reports whether an operation ran (`success`, `partial`, `source_error`, `source_unavailable`, `source_not_integrated`, `lookup_disabled`, or `not_required`). `source_detail` further distinguishes a complete response, an explicit no-measures response, an incomplete response, an unusable parser result, a parser/upstream error, and stale cached data after a failed refresh. An empty parser result is never represented as authoritative confirmation that no measure applies.

## Evidence Contract

An EvidenceEnvelope v1.2 has `schema_version`, `status`, `subject`, `facts`, `evidence`, `conflicts`, `coverage`, `freshness`, `receipt_id`, `state_fingerprint`, and `generated_at`. The published JSON Schema is [`schemas/evidence-envelope-1.2.schema.json`](schemas/evidence-envelope-1.2.schema.json).

- **Facts** cite evidence record IDs and expose support levels: `authoritative`, `supported`, `derived_supported`, `derived_provisional`, `curated`, or `caller_input`. Caller input is never represented as externally verified. Derived facts carry method/version, input fields, and provisional inputs.
- **Evidence** identifies the source, authority class, retrieval time, source status, and URL when one exists. Caller input and internal calculations are labeled as such.
- **Coverage** lists requested, resolved, and unresolved fields. Missing information stays unknown.
- **Freshness** records observation times and stale status. A historical publication is not treated as a current state assertion.
- **Conflicts** preserve competing values and their sources. Field-specific authority policies can record a deterministic resolution; absent a decisive policy, the result reports conflicting sources.

A `receipt_id` (`fr_…`) identifies one evidence observation. Source retrieval times participate in its SHA-256 hash. A `state_fingerprint` (`fs_…`) identifies the substantive state across observations; it excludes retrieval time, receipt ID, evidence IDs, and ordering. Both are deterministic. Receipts live in SQLite and can be fetched with `factrail_get_receipt`. [Hash and migration details](docs/evidence-core.md).

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

The HTTP server defaults to port 8765. Use `FACTRAIL_HOST` and `FACTRAIL_PORT` to change its bind address and port. The MCP endpoint is `POST http://localhost:8765/mcp`; health is at `/healthz`. For stdio clients, run `python3 -m factrail.mcp_server`.

The INSEE key is available through the [INSEE API portal](https://portail-api.insee.fr/). The default SQLite path is `/tmp/factrail_cache.db`; set `FACTRAIL_CACHE_PATH` to a persistent location for durable receipts. The server also has per-client rate limiting and stale cache fallback for company lookups.

## Sources and scope

- **French companies:** INSEE Sirene registry data and BODACC legal notices.
- **EU imports:** the existing Trade engine, curated tariff references, VAT references, and explicit unavailable-source markers. France is the best-supported destination. Some live tariff and market-access adapters are not yet integrated.

### Official EU TARIC snapshot foundation

The Commission identifies TARIC as the integrated EU customs tariff database and states that raw data is freely downloadable in Excel. FACTRAIL treats a supplied, validated Commission snapshot as the primary customs data source. TARIC is a customs data product, not legislation; preserved legal references point to the applicable legal acts, and the Official Journal remains the binding publication. [Commission TARIC page](https://taxation-customs.ec.europa.eu/online-services/online-services-and-databases-customs/eu-customs-tariff-taric_en) · [Commission extraction guide](https://circabc.europa.eu/sd/a/3d892b27-176f-4b8c-bbf5-4e0ee63f7e5a/Explanation%20for%20the%20Taric%20database%20extractions.pdf).

Access2Markets remains a secondary source and cross-check. Its cached HTML is explicitly named and identified as Access2Markets; it is never labelled official TARIC evidence. Curated FACTRAIL tariff values remain an explicit provisional fallback. An official snapshot is not bundled or installed by default, so official TARIC evidence is reported unavailable until an operator ingests Commission files.

The current official page links to the public CIRCABC group, but the linked group route and the cited library-item route returned HTTP 404 in this discovery check. The data.europa.eu catalog exposes only the CIRCABC group URL, not a direct file URL or manifest. An anonymous CIRCABC REST node lookup returned 401. Therefore `fetch` is deliberately reported unavailable; automatic acquisition is not fabricated. Manual file ingestion and local indexed lookup do not use the network. Daily-update delta application is deferred to v2.4B.

Import the monthly Commission Excel package or a ZIP/directory containing its workbooks:

```bash
python3 -m factrail.trade.taric_sync ingest /path/to/taric-snapshot.zip --reference-date YYYY-MM-DD
python3 -m factrail.trade.taric_sync status
```

Full ingestion validates nomenclature, declarable codes, import measures, geographical descriptions and membership, exclusions, conditions, measure footnotes, legal bases, additional codes, certificates, footnotes, and relevant TARIC business-code tables. `--partial` is an explicit reduced-scope option; results remain marked partial. The importer computes per-file and package SHA-256 hashes, builds an indexed SQLite database, validates it, and atomically switches the active pointer. A failed refresh keeps the active database and marks it stale. `FACTRAIL_TARIC_SNAPSHOT_DIR` selects the local store directory. Synthetic Excel fixtures in tests are test-only and are never loaded as production tariff evidence.

The first query returns raw measure expressions and linked applicability details for `(goods_code, origin_country, assessment_date)`, including parent-code measures. It checks official nomenclature and declarable-code records; code width alone does not establish a valid customs code. Access2Markets and curated rates cannot upgrade missing official snapshot evidence. No duty calculation or final landed-cost claim is made from this snapshot foundation.

FACTRAIL does not perform general web search or natural-language claim verification. The Evidence Core currently has one verification resolver (`company_fr`) and one assessment type (`import`).

## Development

```bash
python3 -m pytest -q
python3 -m compileall -q factrail tests
python3 -m factrail.evidence.demand --days 7
```

The offline suite uses mocks and fixtures. Live tests use the project's `--live` convention and require external access. The private demand report aggregates capability gaps without storing identifiers, company names, product descriptions, prompts, or full request payloads. [Evidence Core design](docs/evidence-core.md).

For operators: [deployment checklist](docs/deployment-checklist.md). Release history: [changelog](CHANGELOG.md).

An isolated [Apify Actor wrapper](apify/README.md) is prepared as a distribution and payment channel. It calls this canonical MCP service and does not change the FACTRAIL backend or the direct MCP endpoint.

## License

The code and documentation in this repository are licensed under [Apache License 2.0](LICENSE). Source data remains subject to its publishers' terms, including the INSEE and BODACC open-data licenses.
