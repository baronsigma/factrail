# Changelog

## 2.1.1 — Public release hardening

- Tightened receipt ID validation and public MCP schemas and descriptions.
- Made SQLite cache initialization non-destructive and repeatable; improved readiness and demand telemetry safeguards.
- Updated quickstart, agent documentation, deployment guidance, and release materials.

## 2.1.0 — Trade Evidence, authority policies, demand telemetry

- Added import assessments to the Evidence Core through the existing Trade engine.
- Added state fingerprints, field-scoped authority policies, and private aggregate demand telemetry.
- Added `factrail_assess` while preserving `assess_import`.

## 2.0.0 — Evidence Core

- Introduced EvidenceEnvelope, French company resolution, deterministic persistent receipts, and `factrail_verify` and `factrail_get_receipt`.
- Preserved existing company and Trade tool responses.
