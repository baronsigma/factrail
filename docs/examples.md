# MCP request examples

These are request shapes, not claimed live results. Replace placeholders with caller-supplied identifiers and values. The live `factrail_capabilities` response and MCP `tools/list` define deployed support.

## A. Discover capabilities

Call `factrail_capabilities` with an empty argument object:

```json
{}
```

Inspect capability IDs, operation, version, required/optional inputs, fields, sources, source installation state, and limitations before selecting an operation.

## B. Verify a French company

Call `factrail_verify`:

```json
{
  "subject_type": "company_fr",
  "identifier": "<SIREN or SIRET supplied for the lookup>",
  "fields": ["status", "legal_name", "head_office"]
}
```

Source availability and record coverage determine which facts can be returned. Current INSEE/Sirene registry data has priority for current status and legal name; lower-priority evidence can remain visible.

## C. Retrieve a receipt

Use the actual `receipt_id` from a previous FACTRAIL response; do not construct or guess one:

```json
{"receipt_id":"<receipt_id returned by the prior operation>"}
```

`factrail_get_receipt` recomputes the canonical content hash and rejects altered receipt content. This is content integrity checking, not a digital signature or proof of truth.

## D. Assess an import (Beta)

Call `factrail_assess`:

```json
{
  "assessment_type": "import",
  "parameters": {
    "product": "<caller-provided product description>",
    "origin_country": "<ISO 3166-1 alpha-2 country>",
    "destination_country": "<EU destination country>",
    "quantity": 1,
    "goods_value": 100,
    "currency": "EUR",
    "known_hs_code": "<optional caller-provided code>"
  }
}
```

All values above are placeholders or illustrative caller inputs, not assessed facts or tariff results. Import support is Beta. Assessment may be partial or provisional; no genuine official Commission TARIC snapshot is currently installed and authoritative EU VAT is not integrated. Review support levels, evidence, coverage, freshness, conflicts, and unresolved dependencies in the actual response.
