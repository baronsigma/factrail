# FACTRAIL v2.4.0A — Official TARIC Snapshot Foundation

## Official distribution discovery

Checked 2026-09-28.

- The current [DG TAXUD TARIC page](https://taxation-customs.ec.europa.eu/online-services/online-services-and-databases-customs/eu-customs-tariff-taric_en) links “TARIC raw data” to the public CIRCABC group and states that raw data is freely available in Excel format.
- The current [Commission extraction guide](https://circabc.europa.eu/sd/a/3d892b27-176f-4b8c-bbf5-4e0ee63f7e5a/Explanation%20for%20the%20Taric%20database%20extractions.pdf) is directly downloadable as a PDF (HTTP 200). Its accessible copy reports an update on 09/06/2026. It describes monthly extracts with reference date on the first day of the month, publication on the first Commission working day, and daily update ZIPs as a separate, trial-phase complement. The daily set in this newer copy contains Add_Codes, Certificates, Footnotes, Goods_Nomenclature, Measure_Conditions, Measure_Footnotes, and Measures. (The 2025 revision named in the implementation brief listed five tables.)
- The Commission page’s CIRCABC link resolves to `https://circabc.europa.eu/w/browse/0e5f18c2-4b2f-42e9-aed4-dfe50ae1263b`; it returned HTTP 404. The cited group UI route and deep item route for library object `177bcf69-726c-4600-99f9-c2863cb2057b` also returned HTTP 404. The corresponding anonymous CIRCABC REST node/content routes returned HTTP 404. A previous deep URL is likewise stale. These errors establish that the routes tested are unavailable, not that the files ceased to exist.
- [data.europa.eu TARIC Consultation catalog](https://data.europa.eu/data/datasets/taric-consultation?locale=en) contains two distributions. “TARIC raw data” is cataloged as HTML with no direct download URL or machine-readable file manifest. The 2025 Lithuanian public-sector response at [data.gov.lt](https://data.gov.lt/requests/14466/) points to the specified public group and item as the place to obtain the CN rates.
- The official group and object identity are corroborated by public documentation, but no stable direct file UUID download, anonymous REST API, or structured manifest was verified. Automatic fetch is therefore not reliable and remains unsupported; the supported mechanism is operator-supplied official XLSX/ZIP ingestion. No private endpoint or human-rendered consultation table is scraped.

The guide’s documented table titles and columns, rather than guessed export filenames alone, define normalization. Filenames/sheet titles may identify a table when they match official guide terms. Daily deltas are intentionally not applied; see v2.4B scope.

## Snapshot ingestion

`python3 -m factrail.trade.taric_sync ingest PATH --reference-date YYYY-MM-DD [--partial]` accepts a workbook, a directory tree of `.xlsx` files, or a ZIP of `.xlsx` files. Parsing is offline. The manifest records publisher, source, reference date, retrieval time, individual file hashes, package hash, format/ingestion versions, partial scope, and file counts. XLSX parse errors, absent core tables, missing full-scope tables, and empty nomenclature/measures are rejected. `--partial` is explicit and results remain provisional. The imported rows retain original columns and file/sheet row references alongside normalized fields.

Each accepted snapshot is built into a new SQLite database with lookup indexes, integrity-checked and sanity-checked before an atomic pointer update. Content-addressed database files leave the current active database untouched on parse/build failure. `previous.json` retains the previous active snapshot; failed refreshes retain the active snapshot and write stale metadata. `status` returns active and last-good manifests and row counts. The source files are represented by per-file hashes and preserved as manifest provenance; they are not copied into the indexed store.

## TARIC data model and query

The store indexes raw source rows without discarding their original extracted columns. Normalized dimensions include goods code, technical product-line suffix, measure type, area, validity, additional code, regulation, country, exclusions, conditions, certificates, and footnotes. Indexes cover goods code/suffix, measure, geography, validity, additional code, regulation, table+goods, country+area, area, and excluded area.

Query path: `(goods_code, origin_country, assessment_date)`. It returns source measures, preserved duty expressions, legal references, conditions, additional codes, footnotes, area applicability candidates, classification hierarchy/declarable status, snapshot identity/hash/reference date, and parent-level measures. Measures are joined through their origin/group and exclusion records, and related conditions/footnotes are retained. No complete duty-expression evaluation is claimed. The evidence path attaches the query payload only when an installed snapshot is queried; evidence metadata carries publisher, official source identity, snapshot reference/retrieval date, content hash, and row/measure identifiers. Absent official data is `source_unavailable`; Access2Markets remains secondary; curated rates remain curated/provisional.

No real TARIC snapshot was available locally during implementation, so there are no real source row counts or live query example. Deterministic fixture row counts are synthetic TEST DATA and are not presented as a Commission result.

## Source separation

The old `TaricStore` compatibility name pointed to Access2Markets HTML parsing/cache. It is now explicitly a deprecated compatibility alias for `Access2MarketsStore`; the implementation and cache table use Access2Markets identity, and official data is a separate `OfficialTaricStore`. Evidence labels, publisher, and authority class distinguish Access2Markets secondary results, Commission snapshot measures, and curated fallback values. No Access2Markets evidence is labelled authoritative TARIC.

## Verification

`python3 -m pytest -q`: **304 passed, 10 skipped**, one third-party Starlette deprecation warning. Snapshot-specific deterministic tests cover manifests/hashes, partial/full validation, malformed files, atomic activation, last-good/stale behavior, hierarchy, parent measure inheritance, geography/exclusions, dates, conditions, additional codes, legal references, ad valorem and preserved specific expressions, source identity, and CLI status/ingest.

## Risks and deferred work

- No official workbook was obtained, so exact source workbook header variants still require validation against a supplied current Commission extract; guide-based synthetic fixtures are not proof of live compatibility.
- The current CIRCABC browse/library and REST routes failed in the tested environment. Automatic acquisition remains unavailable until the Commission exposes a verifiable download route/manifest.
- Daily delta application is deferred to v2.4B. Monthly snapshots can omit changes after extraction until the reference date; snapshot reference and retrieval dates are exposed.
- Complex conditional/composite duties are preserved as expressions and conditions, not calculated. This release does not create authoritative final duty totals.
