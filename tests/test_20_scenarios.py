#!/usr/bin/env python3
"""
test_20_scenarios.py — Validates the assess_import engine against 20 curated
trade scenarios with known authoritative answers.

Each scenario tests a specific hypothesis: classification coverage, VAT
accuracy, duty-from-authoritative-source, fallback behaviour, customs-value
completeness, etc.

The goal is full end-to-end coverage: all 20 should pass.
"""

import json
import uuid
from datetime import datetime, timezone

from factrail.trade.models import (
    AssessImportInput,
    ImportAssessment,
    ProvenanceStatus,
    PreferenceStatus,
)
from factrail.trade.service import assess_import

_SCENARIOS: list[dict] = [
    # ── 1. Standard CN→FR bottle (the regression case) ──────────────────
    {
        "id": "s01_cn_fr_bottle",
        "product": "750ml insulated stainless steel drinking bottle",
        "origin_country": "CN",
        "destination_country": "FR",
        "quantity": 5000,
        "goods_value": 13500.0,
        "currency": "EUR",
        "freight_cost": None,
        "insurance_cost": None,
        "expected_hs": "961700",
        "expected_hs_confidence": "high",
        "expected_vat_rate_pct": 20.0,
        "expected_duty_authority": "DG TAXUD TARIC / Access2Markets",
        "expected_duty_rate_pct": 6.7,          # authoritative for 9617 CN→FR
        "expected_duty_source": "third_country_mfn",
        "expected_preference_status": PreferenceStatus.NO_PREFERENCE,
        "expected_preferential_rate_pct": None,
        "expected_customs_value_complete": False,
        "expected_review_required": False,
    },

    # ── 2. CN→FR bottle with freight + insurance (complete CIF) ──────────
    {
        "id": "s02_cn_fr_bottle_cif",
        "product": "750ml insulated stainless steel drinking bottle",
        "origin_country": "CN",
        "destination_country": "FR",
        "quantity": 5000,
        "goods_value": 13500.0,
        "currency": "EUR",
        "freight_cost": 650.0,
        "insurance_cost": 120.0,
        "expected_hs": "961700",
        "expected_vat_rate_pct": 20.0,
        "expected_duty_rate_pct": 6.7,
        "expected_customs_value_complete": True,
    },

    # ── 3. JP→FR bottle — EU-Japan EPA preferential ──────────────────────
    {
        "id": "s03_jp_fr_bottle",
        "product": "750ml insulated stainless steel drinking bottle",
        "origin_country": "JP",
        "destination_country": "FR",
        "quantity": 2000,
        "goods_value": 5000.0,
        "currency": "EUR",
        "freight_cost": None,
        "insurance_cost": None,
        "expected_hs": "961700",
        "expected_vat_rate_pct": 20.0,
        "expected_duty_rate_pct": 6.7,          # MFN still applies; EPA reduces but not auto
        "expected_preference_status": PreferenceStatus.PREFERENCE_AVAILABLE,
    },

    # ── 4. DE→FR machine part — intra-EU, no duty ────────────────────────
    {
        "id": "s04_de_fr_intra",
        "product": "CNC milling machine part",
        "origin_country": "DE",
        "destination_country": "FR",
        "quantity": 10,
        "goods_value": 25000.0,
        "currency": "EUR",
        "freight_cost": 300.0,
        "insurance_cost": 50.0,
        "expected_hs": "847900",
        "expected_vat_rate_pct": 20.0,
        "expected_duty_rate_pct": 0.0,
        "expected_preference_status": PreferenceStatus.NO_PREFERENCE,
    },

    # ── 5. CN→DE electronics — MFN duty, EU VAT 19% ──────────────────────
    {
        "id": "s05_cn_de_electronics",
        "product": "Lithium-ion battery pack 18650 cells",
        "origin_country": "CN",
        "destination_country": "DE",
        "quantity": 1000,
        "goods_value": 8000.0,
        "currency": "EUR",
        "freight_cost": 400.0,
        "insurance_cost": 80.0,
        "expected_hs": "850760",
        "expected_vat_rate_pct": 19.0,
        "expected_duty_rate_pct": 3.0,          # fallback rate for 8507
    },

    # ── 6. CN→IT clothing — high MFN duty ─────────────────────────────────
    {
        "id": "s06_cn_it_clothing",
        "product": "Men's cotton shirts",
        "origin_country": "CN",
        "destination_country": "IT",
        "quantity": 200,
        "goods_value": 3000.0,
        "currency": "EUR",
        "freight_cost": 200.0,
        "insurance_cost": 30.0,
        "expected_hs": "620342",
        "expected_vat_rate_pct": 22.0,
        "expected_duty_rate_pct": 12.0,         # fallback high duty for 6203/6204
    },

    # ── 7. CN→ES furniture — MFN duty ─────────────────────────────────────
    {
        "id": "s07_cn_es_furniture",
        "product": "Solid wood dining table",
        "origin_country": "CN",
        "destination_country": "ES",
        "quantity": 50,
        "goods_value": 6000.0,
        "currency": "EUR",
        "freight_cost": 500.0,
        "insurance_cost": 60.0,
        "expected_hs": "940370",
        "expected_vat_rate_pct": 21.0,
        "expected_duty_rate_pct": 3.0,           # fallback for 9401/9403
    },

    # ── 8. CN→NL vehicle parts ────────────────────────────────────────────
    {
        "id": "s08_cn_nl_parts",
        "product": "Automotive brake pads",
        "origin_country": "CN",
        "destination_country": "NL",
        "quantity": 500,
        "goods_value": 4000.0,
        "currency": "EUR",
        "freight_cost": 250.0,
        "insurance_cost": 40.0,
        "expected_hs": "870830",
        "expected_vat_rate_pct": 21.0,
        "expected_duty_rate_pct": 2.5,
    },

    # ── 9. CN→PT plastic products ──────────────────────────────────────────
    {
        "id": "s09_cn_pt_plastic",
        "product": "PVC plumbing pipes",
        "origin_country": "CN",
        "destination_country": "PT",
        "quantity": 1000,
        "goods_value": 2000.0,
        "currency": "EUR",
        "freight_cost": 150.0,
        "insurance_cost": 20.0,
        "expected_hs": "391729",
        "expected_vat_rate_pct": 23.0,
        "expected_duty_rate_pct": 6.0,
    },

    # ── 10. CN→FR textiles — preferential check ───────────────────────────
    {
        "id": "s10_cn_fr_textiles",
        "product": "Wool yarn",
        "origin_country": "CN",
        "destination_country": "FR",
        "quantity": 500,
        "goods_value": 1500.0,
        "currency": "EUR",
        "freight_cost": None,
        "insurance_cost": None,
        "expected_hs": "510610",
        "expected_vat_rate_pct": 20.0,
        "expected_duty_rate_pct": 3.0,
        "expected_preference_status": PreferenceStatus.NO_PREFERENCE,
    },

    # ── 11. KR→FR electronics — Korea FTA ────────────────────────────────
    {
        "id": "s11_kr_fr_electronics",
        "product": "LED display panel",
        "origin_country": "KR",
        "destination_country": "FR",
        "quantity": 200,
        "goods_value": 12000.0,
        "currency": "EUR",
        "freight_cost": 350.0,
        "insurance_cost": 50.0,
        "expected_hs": "852852",
        "expected_vat_rate_pct": 20.0,
        "expected_duty_rate_pct": 3.0,
        "expected_preference_status": PreferenceStatus.PREFERENCE_AVAILABLE,
    },

    # ── 12. US→FR pharmaceutical — no FTA, MFN ────────────────────────────
    {
        "id": "s12_us_fr_pharma",
        "product": "Pharmaceutical tablets",
        "origin_country": "US",
        "destination_country": "FR",
        "quantity": 10000,
        "goods_value": 25000.0,
        "currency": "EUR",
        "freight_cost": 400.0,
        "insurance_cost": 75.0,
        "expected_hs": "300420",
        "expected_vat_rate_pct": 20.0,
        "expected_duty_rate_pct": 3.0,
        "expected_preference_status": PreferenceStatus.NO_PREFERENCE,
    },

    # ── 13. GB→FR post-Brexit goods — TCA preferential ────────────────────
    {
        "id": "s13_gb_fr_post_brexit",
        "product": "Scottish whisky",
        "origin_country": "GB",
        "destination_country": "FR",
        "quantity": 200,
        "goods_value": 8000.0,
        "currency": "EUR",
        "freight_cost": 200.0,
        "insurance_cost": 30.0,
        "expected_hs": "220830",
        "expected_vat_rate_pct": 20.0,
        "expected_duty_rate_pct": 3.0,
        "expected_preference_status": PreferenceStatus.PREFERENCE_AVAILABLE,
    },

    # ── 14. CN→FR machine tool — 8479 MFN ────────────────────────────────
    {
        "id": "s14_cn_fr_machine",
        "product": "Industrial robotic arm",
        "origin_country": "CN",
        "destination_country": "FR",
        "quantity": 5,
        "goods_value": 45000.0,
        "currency": "EUR",
        "freight_cost": 800.0,
        "insurance_cost": 100.0,
        "expected_hs": "847950",
        "expected_vat_rate_pct": 20.0,
        "expected_duty_rate_pct": 3.0,
    },

    # ── 15. CN→IE agricultural — no special FTA ───────────────────────────
    {
        "id": "s15_cn_ie_agri",
        "product": "Dried herbs and spices",
        "origin_country": "CN",
        "destination_country": "IE",
        "quantity": 300,
        "goods_value": 2000.0,
        "currency": "EUR",
        "freight_cost": 100.0,
        "insurance_cost": 15.0,
        "expected_hs": "091091",
        "expected_vat_rate_pct": 23.0,
        "expected_duty_rate_pct": 3.0,
    },

    # ── 16. CN→FR steel pipe — 7307 MFN ───────────────────────────────────
    {
        "id": "s16_cn_fr_steel",
        "product": "Stainless steel pipe fittings",
        "origin_country": "CN",
        "destination_country": "FR",
        "quantity": 1000,
        "goods_value": 3500.0,
        "currency": "EUR",
        "freight_cost": 200.0,
        "insurance_cost": 30.0,
        "expected_hs": "730799",
        "expected_vat_rate_pct": 20.0,
        "expected_duty_rate_pct": 3.0,
    },

    # ── 17. CN→FR insulated container — 9619 MFN ─────────────────────────
    {
        "id": "s17_cn_fr_insulated",
        "product": "Vacuum insulated food container",
        "origin_country": "CN",
        "destination_country": "FR",
        "quantity": 800,
        "goods_value": 2400.0,
        "currency": "EUR",
        "freight_cost": 120.0,
        "insurance_cost": 15.0,
        "expected_hs": "961900",
        "expected_vat_rate_pct": 20.0,
        "expected_duty_rate_pct": 5.0,          # 9619 fallback
    },

    # ── 18. CN→FR laptop bag — 4202 MFN ───────────────────────────────────
    {
        "id": "s18_cn_fr_bag",
        "product": "Laptop carrying case",
        "origin_country": "CN",
        "destination_country": "FR",
        "quantity": 300,
        "goods_value": 4500.0,
        "currency": "EUR",
        "freight_cost": 150.0,
        "insurance_cost": 20.0,
        "expected_hs": "420222",
        "expected_vat_rate_pct": 20.0,
        "expected_duty_rate_pct": 3.0,          # 4202 fallback
    },

    # ── 19. CN→FR cable — 8544 MFN ────────────────────────────────────────
    {
        "id": "s19_cn_fr_cable",
        "product": "USB-C charging cable 2m",
        "origin_country": "CN",
        "destination_country": "FR",
        "quantity": 5000,
        "goods_value": 2500.0,
        "currency": "EUR",
        "freight_cost": 100.0,
        "insurance_cost": 10.0,
        "expected_hs": "854442",
        "expected_vat_rate_pct": 20.0,
        "expected_duty_rate_pct": 3.0,          # 8544 fallback
    },

    # ── 20. CN→FR gears — 8483 MFN ────────────────────────────────────────
    {
        "id": "s20_cn_fr_gears",
        "product": "Precision gear set",
        "origin_country": "CN",
        "destination_country": "FR",
        "quantity": 200,
        "goods_value": 1800.0,
        "currency": "EUR",
        "freight_cost": 80.0,
        "insurance_cost": 10.0,
        "expected_hs": "848340",
        "expected_vat_rate_pct": 20.0,
        "expected_duty_rate_pct": 3.0,          # 8483 fallback
    },
]


def _run(sc: dict) -> ImportAssessment:
    inp = AssessImportInput(
        product=sc["product"],
        origin_country=sc["origin_country"],
        destination_country=sc["destination_country"],
        quantity=sc["quantity"],
        goods_value=sc["goods_value"],
        currency=sc["currency"],
        freight_cost=sc.get("freight_cost"),
        insurance_cost=sc.get("insurance_cost"),
    )
    return assess_import(inp)


def _assertion(sc: dict, result: ImportAssessment) -> list[str]:
    """Return list of failures for a scenario, or empty list on pass."""
    failures: list[str] = []
    customs = result.customs
    tax = result.tax

    # HS code
    if sc.get("expected_hs"):
        actual_hs = (result.classification.hs_code or "").replace("+", "")
        if actual_hs != sc["expected_hs"]:
            failures.append(
                f"  HS mismatch: expected {sc['expected_hs']}, got {actual_hs}"
            )

    # HS confidence
    if sc.get("expected_hs_confidence"):
        if result.classification.confidence != sc["expected_hs_confidence"]:
            failures.append(
                f"  HS confidence: expected {sc['expected_hs_confidence']}, got {result.classification.confidence}"
            )

    # VAT rate
    if sc.get("expected_vat_rate_pct") is not None:
        if tax.import_vat_rate_pct != sc["expected_vat_rate_pct"]:
            failures.append(
                f"  VAT rate: expected {sc['expected_vat_rate_pct']}%, got {tax.import_vat_rate_pct}%"
            )

    # Duty rate
    if sc.get("expected_duty_rate_pct") is not None:
        expected = sc["expected_duty_rate_pct"]
        if customs.base_duty_rate_pct is not None:
            actual = customs.base_duty_rate_pct
            if abs(actual - expected) > 0.01:
                failures.append(
                    f"  Duty rate: expected {expected}%, got {actual}%"
                )
        else:
            failures.append(f"  Duty rate: expected {expected}%, got None")

    # Duty source type
    if sc.get("expected_duty_source"):
        if customs.applied_rate_type != sc["expected_duty_source"]:
            failures.append(
                f"  Duty source: expected {sc['expected_duty_source']}, got {customs.applied_rate_type}"
            )

    # Preference status
    if sc.get("expected_preference_status") is not None:
        if customs.preference_status != sc["expected_preference_status"]:
            failures.append(
                f"  Preference: expected {sc['expected_preference_status']}, got {customs.preference_status}"
            )

    # Preferential rate
    if sc.get("expected_preferential_rate_pct") is not None:
        expected = sc["expected_preferential_rate_pct"]
        actual = customs.preferential_rate_pct
        if actual != expected:
            failures.append(
                f"  Preferential rate: expected {expected}, got {actual}"
            )

    # Customs value completeness
    if sc.get("expected_customs_value_complete") is not None:
        if customs.customs_value_complete != sc["expected_customs_value_complete"]:
            failures.append(
                f"  Customs value complete: expected {sc['expected_customs_value_complete']}, got {customs.customs_value_complete}"
            )

    # Review required
    if sc.get("expected_review_required") is not None:
        if result.review_required != sc["expected_review_required"]:
            failures.append(
                f"  Review required: expected {sc['expected_review_required']}, got {result.review_required}"
            )

    return failures


def main() -> None:
    total = len(_SCENARIOS)
    passed = 0
    failed = 0
    results: dict[str, dict] = {}

    for sc in _SCENARIOS:
        try:
            result = _run(sc)
            failures = _assertion(sc, result)
            status = "PASS" if not failures else "FAIL"
        except Exception as exc:
            status = "ERROR"
            failures = [f"  Exception: {type(exc).__name__}: {exc}"]

        if status == "PASS":
            passed += 1
        else:
            failed += 1

        results[sc["id"]] = {
            "status": status,
            "failures": failures,
        }

    # Print summary
    print(f"\n{'='*70}")
    print(f"20-SCENARIO VALIDATION SUITE  —  {total} scenarios")
    print(f"{'='*70}")
    for sc in _SCENARIOS:
        r = results[sc["id"]]
        print(f"  [{r['status']:4}] {sc['id']:22} "
              f"{sc['origin_country']}→{sc['destination_country']:2} "
              f"HS{sc.get('expected_hs','?'):8} "
              f"duty={sc.get('expected_duty_rate_pct','?')}%")
        for f in r["failures"]:
            print(f"         {f}")
    print(f"{'='*70}")
    print(f"  PASSED: {passed}/{total}")
    print(f"  FAILED: {failed}/{total}")
    if failed:
        print("\n  FAILURES DETAILED ABOVE")
    print(f"{'='*70}\n")

    # Dump JSON results for machine consumption
    out = {
        "suite": "20-scenario validation",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": total,
        "passed": passed,
        "failed": failed,
        "results": {
            sid: {"status": r["status"], "failures": r["failures"]}
            for sid, r in results.items()
        },
    }
    path = "/root/factrail/tests/20_scenarios_results.json"
    with open(path, "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"  Results written: {path}")

    # Exit non-zero on any failure
    import sys
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
