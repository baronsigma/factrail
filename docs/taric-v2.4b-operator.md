# TARIC snapshot acceptance and operations (v2.4.0B)

This workflow accepts only operator-supplied European Commission TARIC files. It does not download or activate synthetic fixtures. No official data is bundled. Until an official package passes doctor and ingestion, official TARIC status is `not_installed` and Access2Markets remains secondary evidence.

## Operator workflow

1. Obtain a genuine monthly TARIC extraction from the European Commission / DG TAXUD CIRCABC group **TARIC and Quota data and information**. Keep the original ZIP/XLSX package unchanged.
2. Copy it to an operator-controlled server path outside the Git checkout, for example `/srv/factrail/imports/taric-2026-10.zip`.
3. Inspect without activation. JSON is the default; add `--human` for a short summary. Use `--mode compatible` to permit only centrally listed, unambiguous aliases. Every alias adaptation appears in the report. `strict` accepts only canonical semantic header names. Review the persisted report under the configured store's `acceptance/` directory.

   ```bash
   python3 -m factrail.trade.taric_sync doctor /srv/factrail/imports/taric-2026-10.zip
   python3 -m factrail.trade.taric_sync doctor /srv/factrail/imports/taric-2026-10.zip --human
   ```

4. Resolve every rejection and inspect warnings, unknown columns/tables, expression patterns, duplicate identifiers, malformed rows, and semantic reference checks. Doctor never activates data.
5. Ingest the exact package and its official reference date. Ingestion rechecks the package hash and accepted report, builds a staged SQLite index, performs semantic/integrity checks, persists the completed acceptance report, then atomically changes the active pointer.

   ```bash
   python3 -m factrail.trade.taric_sync ingest /srv/factrail/imports/taric-2026-10.zip --reference-date 2026-10-01 --mode compatible
   ```

   A knowingly reduced package requires both `doctor --partial` and `ingest --partial`; it remains marked partial and cannot claim full applicability coverage.

6. Verify installation and retain the row-free fingerprint for change tracking.

   ```bash
   python3 -m factrail.trade.taric_sync status
   python3 -m factrail.trade.taric_sync fingerprint
   ```

7. After acceptance, run a local query for a known code, origin, and assessment date. The query returns raw/normalized measures and does not calculate complex duties.

   ```bash
   python3 - <<'PY'
   from factrail.trade.official_taric_store import OfficialTaricStore
   result = OfficialTaricStore().query("8508700010", "CN", "2026-10-01")
   print(result["source"], result["source_outcome"], result.get("snapshot", {}).get("sha256"))
   print(result.get("classification"))
   print(result.get("measures", []))
   PY
   ```

## Data and operations

The default local store is `~/.factrail/taric_snapshots`; set `FACTRAIL_TARIC_SNAPSHOT_DIR` to use a durable operator-managed volume. It contains content-addressed SQLite indexes, `active.json`, `previous.json`, failed-refresh/validation markers, and persistent acceptance reports. Source workbooks remain at their supplied location; their original filenames, individual hashes, package hash, import/retrieval time, reference date, parser version, and report are retained in the manifest. Local TARIC data paths are ignored by Git.

`status` and the dynamic `factrail_capabilities` response report `not_installed`, `installed_current`, `installed_stale`, or `validation_failed`. An unavailable optional TARIC dataset does not make `/healthz` unhealthy. Last-good evidence remains in place when a candidate fails.

Automatic acquisition is currently unavailable because the Commission's linked CIRCABC browse/library routes and the anonymous routes tested during implementation did not provide a reliable direct download or manifest. The Commission raw-data listing and public file references still exist in official/public documentation; this is an access/discovery limitation, not evidence of dataset removal, and may change.

Unknown duty expressions are inventoried and preserved in the imported rows. A pattern that is not a simple ad-valorem expression is not calculated by this release. This harness does not expand duty calculation, VAT, or another vertical.
