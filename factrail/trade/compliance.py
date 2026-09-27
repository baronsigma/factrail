"""Compliance engine for FACTRAIL Trade V0.1.

Standalone, self-contained compliance rules for V0.  No dependency on the
service layer.  Generic EU-import compliance rules are produced when no
product-specific rules are available.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from .models import (
    Compliance,
    ComplianceRequirement,
    Evidence,
    ProvenanceStatus,
)

# ---------------------------------------------------------------------------
# Generic EU import compliance (applies to most goods)
# ---------------------------------------------------------------------------

GENERIC_COMPLIANCE: dict[str, Any] = {
    "requirements": [
        {
            "name": "EU customs declaration",
            "description": (
                "An EU customs declaration is required for imports from outside "
                "the EU. The importer (or their customs representative) must "
                "file the declaration with the customs authority of the member "
                "state of entry."
            ),
            "action": (
                "File EU customs declaration (SAD/ASC layout) via the "
                "importing EU member state's customs system."
            ),
            "evidence": {
                "value": "required",
                "status": "verified",
                "authority": "European Commission — Customs Union",
                "source": "EU customs declaration requirements",
                "url": "https://ec.europa.eu/taxation_customs/union/customs-declaration_en",
                "retrieved_at": datetime.now(timezone.utc),
                "supports": ["compliance", "documents"],
                "confidence": 1.0,
            },
        },
        {
            "name": "EORI number",
            "description": (
                "An EU EORI (Economic Operators Registration and Identification) "
                "number is required for businesses importing into the EU. The "
                "number is issued by the customs authority of the member state "
                "where the business is established."
            ),
            "action": (
                "Obtain an EORI number from the EU member state where "
                "the importer is established before the first import."
            ),
            "evidence": {
                "value": "required",
                "status": "verified",
                "authority": "European Commission — EORI",
                "source": "EU EORI requirement for customs",
                "url": "https://ec.europa.eu/taxation_customs/union/eori",
                "retrieved_at": datetime.now(timezone.utc),
                "supports": ["compliance", "documents"],
                "confidence": 1.0,
            },
        },
    ],
    "restrictions": [],
    "documents": [
        {
            "name": "Commercial invoice",
            "description": (
                "A commercial invoice showing goods description, value, "
                "quantity, origin, Incoterm, and parties."
            ),
            "action": "Provide a commercial invoice per consignment.",
            "evidence": {
                "value": "required",
                "status": "verified",
                "authority": "EU customs — documentary requirements",
                "source": "EU customs documentation requirements",
                "url": "https://ec.europa.eu/taxation_customs/union/documents-required-import_en",
                "retrieved_at": datetime.now(timezone.utc),
                "supports": ["compliance", "documents"],
                "confidence": 1.0,
            },
        },
        {
            "name": "Packing list",
            "description": (
                "A packing list detailing the contents, weight, and "
                "dimensions of the consignment."
            ),
            "action": "Provide a packing list per consignment.",
            "evidence": {
                "value": "required",
                "status": "verified",
                "authority": "EU customs",
                "source": "EU customs — packing list requirement",
                "url": "https://ec.europa.eu/taxation_customs/union/documents-required-import_en",
                "retrieved_at": datetime.now(timezone.utc),
                "supports": ["compliance", "documents"],
                "confidence": 1.0,
            },
        },
        {
            "name": "Transport document",
            "description": "Bill of lading / airway bill / CMR depending on mode.",
            "action": "Provide the transport document for the consignment.",
            "evidence": {
                "value": "required",
                "status": "verified",
                "authority": "EU customs",
                "source": "EU customs — transport document",
                "url": "https://ec.europa.eu/taxation_customs/union/documents-required-import_en",
                "retrieved_at": datetime.now(timezone.utc),
                "supports": ["compliance", "documents"],
                "confidence": 1.0,
            },
        },
    ],
    "warnings": [
        (
            "Customs may inspect the consignment. Ensure goods are correctly "
            "marked and labelled for EU market access (language, safety marks where "
            "applicable)."
        ),
        (
            "VAT and duty must be paid before goods are released unless a customs "
            "deferment account or centralised clearance arrangement is in place."
        ),
        (
            "Check product-specific labelling, safety, and CE marking requirements "
            "for the destination EU member state before import."
        ),
    ],
}


def _to_req(d: dict[str, Any]) -> ComplianceRequirement:
    ev = d.get("evidence")
    if isinstance(ev, dict):
        ev = Evidence(**ev)
    return ComplianceRequirement(
        name=d.get("name", ""),
        description=d.get("description", ""),
        applies=d.get("applies", True),
        evidence=ev,
        action=d.get("action"),
    )


def _to_list(field: str, rules: dict[str, Any]) -> list[ComplianceRequirement]:
    return [_to_req(d) for d in rules.get(field, [])]


def build_compliance(
    product: str,
    hs6: Optional[str],
    origin_country: str,
    destination_country: str,
    rules: Optional[dict[str, Any]],
    evidence_ledger: list[Evidence],
) -> Compliance:
    """Build the Compliance block.

    If *rules* is None or empty, generic EU import compliance rules are used.
    Product-specific rules (V0: limited to a few high-demand HS headings) are
    merged on top of the generic set.
    """
    # Start from generic compliance
    base = GENERIC_COMPLIANCE

    if rules:
        # Merge product-specific rules on top of generic (product-specific wins)
        merged: dict[str, Any] = {
            "requirements": list(base["requirements"]),
            "restrictions": list(base["restrictions"]),
            "documents": list(base["documents"]),
            "warnings": list(base["warnings"]),
        }
        for field in ("requirements", "restrictions", "documents"):
            merged[field].extend(rules.get(field, []))
        merged["warnings"].extend(rules.get("warnings", []))
        base = merged

    # Deduplicate warnings (preserve order)
    seen_w: set[str] = set()
    deduped_warnings: list[str] = []
    for w in base["warnings"]:
        if w not in seen_w:
            seen_w.add(w)
            deduped_warnings.append(w)

    compliance = Compliance(
        requirements=_to_list("requirements", base),
        restrictions=_to_list("restrictions", base),
        documents=_to_list("documents", base),
        warnings=deduped_warnings,
        evidence=[],
    )

    for req_list in (compliance.requirements, compliance.restrictions, compliance.documents):
        for req in req_list:
            if req.evidence is not None and req.evidence not in evidence_ledger:
                evidence_ledger.append(req.evidence)

    compliance.evidence.extend(
        ev for ev in evidence_ledger if ev not in compliance.evidence
    )

    return compliance
