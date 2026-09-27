"""Classification engine for FACTRAIL Trade V0.1.

Architecture:
  - HS code  -> 6-digit Harmonized System (WCO, international)
  - CN code  -> 8-digit EU Combined Nomenclature (Commission Implementing Regulation)
  - TARIC   -> 10-digit EU Tariff Integrated Community (additional EU measures)
  - BTI     -> Binding Tariff Information (national, binding on customs)

V0.1 deliberately limits certainty.  The engine produces candidates with
explicit confidence, alternatives, and review_required flags.  It never
presents a generated classification as legally binding.

Classification level ladder:
  1. known_hs_code supplied by caller -> validate, enrich to CN/TARIC where data exists
  2. rule-of-thumb keyword matching against product attributes -> candidate HS codes
  3. attribute-free natural-language description -> broad candidates + high uncertainty
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from .models import (
    Classification,
    ClassificationAlternative,
    Evidence,
    ProvenanceStatus,
)


# ---------------------------------------------------------------------------
# HS code validation
# ---------------------------------------------------------------------------

_HS_DIGIT_RE = re.compile(r"^\d{6,10}$")


def validate_hs_code(code: str) -> bool:
    """Return True if *code* looks like a legal HS/CN/TARIC numeric code.

    Accepts 6–10 digits.  Returns False for anything else (including
    alphanumeric EPS/HS codes such as \"EPS_GrLaA\" — those are out of
    scope for V0 without an external codebook).
    """
    if not code or not isinstance(code, str):
        return False
    return bool(_HS_DIGIT_RE.match(code))


def hs_code_level(code: str) -> Optional[str]:
    """Return 'hs', 'cn', or 'taric' based on code length, or None."""
    digits = len(code)
    if digits == 6:
        return "hs"
    if digits == 8:
        return "cn"
    if digits == 10:
        return "taric"
    return None


def hs_code_to_parent(code: str) -> Optional[str]:
    """Return the 6-digit HS parent of an 8/10-digit CN/TARIC code."""
    if len(code) < 6:
        return None
    return code[:6]


# ---------------------------------------------------------------------------
# Attribute extraction (rule-of-thumb, V0)
# ---------------------------------------------------------------------------

# Candidat HS codes keyed by keyword/attribute signals.
# These are NOT authoritative — they are starting points for
# classification, used to propose candidates when no known_hs_code is given.
# Each entry is (hs6, description, signals) where signals is a list of
# substrings that increase the score for this candidate.
#
# Source of the HS descriptions: WCO HS 2022 nomenclature (summaries).
# We do NOT scrape; these are curated reference strings for V0 candidates.

_HS_CANDIDATES: list[tuple[str, str, list[str]]] = [
    # 9601 — Articles of gold / silver
    ("9601", "Articles of natural or cultured pearls, precious or semi-precious stones",
     ["jewellery", "necklace", "ring", "bracelet", "earring", "pearl", "gemstone"]),

    # 9603 — Brooms, brushes
    ("9603", "Brooks, brushes and mops, of hair, twigs, etc.",
     ["broom", "brush", "mop"]),

    # 9605 — Combs, hair accessories
    ("9605", "Combs, hair slides, hair ornaments",
     ["comb", "hair clip", "hair slide"]),

    # 9606 — Buttons, snap fasteners
    ("9606", "Buttons, snap fasteners, press studs",
     ["button", "snap fastener", "press stud"]),

    # 9611 — Globes, maps
    ("9611", "Globes, maps, navigational instruments",
     ["globe", "map", "navigation"]),

    # 9614 — Cigarette lighters / striking machines
    ("9614", "Cigarette lighters and striking machines",
     ["lighter", "cigarette lighter"]),

    # 9615 — Hair ornaments, wigs
    ("9615", "Artificial hair, wigs, hairpieces",
     ["wig", "hairpiece", "false hair"]),

    # 9616 — Scent sprays, sanitary sprays
    ("9616", "Scent sprays and sanitary sprays",
     ["spray", "sanitary spray"]),

    # 9617 — Vacuum flasks, insulated containers, insulated bottles
    ("9617", "Vacuum flasks and insulated containers",
     ["vacuum flask", "insulated flask", "thermos", "insulated bottle", "insulated container",
      "insulated stainless steel drinking bottle", "hot flask", "bottle", "drinking bottle",
      "steel bottle", "metal bottle", "water bottle"]),

    # 9618 — Massage tables (not relevant)

    # 9619 — Slides, slides for babies (not relevant)

    # 9620 — Breathing appliances (not relevant)

    # 9621 — Lamps, lanterns (not relevant)

    # 9701 — Paintings (not relevant)

    # 9702 — Original sculptures (not relevant)

    # 9703 — Original drawings (not relevant)

    # 9704 — Postal stamps (not relevant)

    # 4202 — Trunks, suitcases, bags, cases, containers
    ("4202", "Trunks, suitcases, vanity cases, briefcases, bags, wallets,"
             " pouches, travelling bags, umbrellas, walking sticks, containers, cases",
     ["bag", "suitcase", "trunk", "luggage", "wallet", "purse", "backpack",
      "handbag", "briefcase", "container", "case", "metal container", "travel bag"]),

    # 6203 — Men's suits, jackets, trousers
    ("6203", "Men's or boys' suits, jackets, blazers, trousers, breeches and shorts (woven)",
     ["suit", "blazer", "jacket", "trouser", "shirt for men", "men's shirt", "men's trousers", "boys' shirt", "boys' suit"]),

    # 6204 — Women's suits, jackets, dresses
    ("6204", "Women's or girls' suits, jackets, dresses, skirts, trousers, shorts (woven)",
     ["dress", "skirt", "blouse", "jacket woman"]),

    # 6205 — Men's shirts
    ("6205", "Men's or boys' shirts (woven)",
     ["shirt men", "men's shirt"]),

    # 6206 — Women's blouses
    ("6206", "Women's or girls' blouses, shirts (woven)",
     ["blouse", "woman shirt"]),

    # 6212 — Brassieres, girdles
    ("6212", "Brassieres, girdles, corsets, etc.",
     ["brassiere", "bra", "corset"]),

    # 6306 — Truck covers, tarpaulins
    ("6306", "Cheesecloth, gauze, canvas, tarpaulins,",
     ["tarpaulin", "canvas", "truck cover"]),

    # 7307 — Tubes and pipes
    ("7307", "Tubes and pipes and fitting thereof of iron or steel",
     ["steel tube", "iron pipe", "steel pipe"]),

    # 7308 — Structures
    ("7308", "Structures and parts of structures of iron or steel",
     ["steel structure", "girder", "tower"]),

    # 8301 — Steel articles (knives, swords)
    ("8301", "Steel swords, knives, daggers, razors,",
     ["steel knife", "sword", "razor"]),

    # 8302 — Staples, clips
    ("8302", "Staples, hooks, screws, rivets,",
     ["staple", "hook", "rivet"]),

    # 8413 — Pumps
    ("8413", "Pumps for liquids, piston pumps, plunger pumps",
     ["pump", "water pump"]),

    # 8415 — Refrigerating equipment
    ("8415", "Refrigerating equipment, refrigerators, freezers,",
     ["refrigerator", "freezer", "fridge"]),

    # 8418 — Refrigerators, freezers, cold stores
    ("8418", "Refrigerators, freezers, cold stores,",
     ["cold store", "cold room"]),

    # 8421 — Centrifuges
    ("8421", "Centrifuges, filtering equipment,",
     ["centrifuge", "filter"]),

    # 8423 — Weighing equipment
    ("8423", "Weighing machinery, balances,",
     ["scale", "balance", "weighing"]),

    # 8450 — Dishwashers
    ("8450", "Household washing machines, dryers,",
     ["dishwasher", "washing machine"]),

    # 8456 — Tools
    ("8456", "Tools for drilling, cutting, grinding,",
     ["drill", "cutter", "grinder"]),

    # 8466 — Parts for machines
    ("8466", "Parts and accessories for machine tools,",
     ["machine part", "tool part"]),

    # 8479 — Machines with individual functions
    ("8479", "Machines and mechanical appliances having individual functions,",
     ["machine function", "individual function"]),

    # 8481 — Taps, cocks
    ("8481", "Taps, cocks, valves and similar appliances,",
     ["tap", "cock", "valve"]),

    # 8482 — Ball bearings
    ("8482", "Ball bearings and roller bearings,",
     ["ball bearing", "roller bearing"]),

    # 8483 — Transmission shafts, cranks
    ("8483", "Transmission shafts, cranks, gears,",
     ["shaft", "crank", "gear"]),

    # 8506 — Primary cells and batteries
    ("8506", "Primary cells and primary batteries,",
     ["battery", "primary battery"]),

    # 8507 — Electric accumulators
    ("8507", "Electric accumulators, including separators,",
     ["accumulator", "rechargeable battery", "lithium-ion"]),

    # 8509 — Electro-mechanical household appliances
    ("8509", "Electro-mechanical domestic appliances,",
     ["household appliance", "vacuum cleaner"]),

    # 8516 — Electric heating
    ("8516", "Electric heating, electric Heating,",
     ["electric heater", "heated blanket"]),

    # 8517 — Telephone sets
    ("8517", "Telephone sets, telephones, smartphones,",
     ["telephone", "smartphone", "mobile phone"]),

    # 8518 — Microphones, loudspeakers
    ("8518", "Microphies, loudpeakers,",
     ["microphone", "loudspeaker"]),

    # 8523 — Recording media
    ("8523", "Recording media for sound or video,",
     ["cd", "dvd", "recording"]),

    # 8525 — Transmission apparatus
    ("8525", "Transmission apparatus for radio/television,",
     ["transmission"]),

    # 8528 — Monitors, projectors
    ("8528", "Monitors, projectors,",
     ["monitor", "screen", "projector"]),

    # 8529 — Parts for TV
    ("8529", "Parts for television, radio,",
     ["tv part", "radio part"]),

    # 8531 — Signaling equipment
    ("8531", "Electrical signaling equipment,",
     ["signal"]),

    # 8536 — Electrical apparatus for switching
    ("8536", "Electrical apparatus for switching, fuses,",
     ["switch", "fuse", "circuit breaker"]),

    # 8537 — Boards, panels
    ("8537", "Boards, panels, control panels,",
     ["control panel", "board"]),

    # 8542 — Electronic integrated circuits
    ("8542", "Electronic integrated circuits,",
     ["integrated circuit", "chip"]),

    # 8543 — Electrical machines
    ("8543", "Electrical machines and apparatus,",
     ["electrical machine"]),

    # 8544 — Insulated wire
    ("8544", "Insulated wire, cable,",
     ["wire", "cable"]),

    # 8708 — Parts for motor vehicles
    ("8708", "Parts and accessories for motor vehicles,",
     ["car part", "motor vehicle part"]),

    # 8715 — Baby transport
    ("8715", "Baby transport equipment,",
     ["baby transport", "stroller"]),

    # 9401 — Seats
    ("9401", "Seats (other than those of heading 9402),",
     ["seat", "chair", "stool"]),

    # 9402 — Medical seats
    ("9402", "Medical, surgical or veterinary seats,",
     ["medical seat"]),

    # 9403 — Other furniture
    ("9403", "Other furniture and parts thereof,",
     ["table", "desk", "bookshelf"]),

    # 9404 — Mattresses
    ("9404", "Mattresses, mattresses pads,",
     ["mattress"]),

    # 9405 — Lamps
    ("9405", "Lamps and lighting accessories,",
     ["lamp", "light fixture"]),

    # 9503 — Tricycles, toy vehicles
    ("9503", "Tricycles, scooters, toy vehicles,",
     ["tricycle", "scooter", "toy car"]),

    # 9504 — Video games
    ("9504", "Video game consoles,",
     ["video game", "console"]),

    # 9508 — Circus equipment
    ("9508", "Circus and funfair equipment,",
     ["circus"]),

    # 9706 — Antiques
    ("9706", "Antique and old or used objects,",
     ["antique"]),
]


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).strip()


def _token_match_score(description: str, signals: list[str]) -> float:
    """Return a 0–1 score for how well *description* matches *signals*."""
    if not description or not signals:
        return 0.0
    text = _normalise(description)
    if not text:
        return 0.0
    matched = sum(1 for s in signals if _normalise(s) in text)
    return matched / max(1, len(signals))


def _candidate_from_hs6(hs6: str, description: str, signals: list[str]) -> ClassificationAlternative:
    score = _token_match_score(description, signals)
    if score <= 0.0:
        return ClassificationAlternative(
            code=hs6, confidence=0.0, reason="No attribute signals matched",
            level="hs"
        )
    return ClassificationAlternative(
        code=hs6,
        confidence=min(1.0, score),
        reason=f"Keyword/attribute match: matched {round(score * 100)}% of reference signals",
        level="hs",
    )


def extract_classification_candidates(
    product: str,
    material: Optional[str] = None,
    known_hs_code: Optional[str] = None,
) -> list[ClassificationAlternative]:
    """Return candidate HS classifications for a product description.

    Priority:
      1. If known_hs_code is supplied and valid, that is the top candidate.
      2. Attribute signals from material + product description produce
         additional candidates from the curated _HS_CANDIDATES list.
    """
    alternatives: list[ClassificationAlternative] = []

    # 1. Known HS code
    if known_hs_code and validate_hs_code(known_hs_code):
        lvl = hs_code_level(known_hs_code) or "hs"
        alts = ClassificationAlternative(
            code=known_hs_code,
            confidence=0.95 if lvl == "hs" else 0.85,
            reason="Provided by caller",
            level=lvl,
        )
        alternatives.append(alts)

    # 2. Attribute-based candidates
    augmented = product
    if material:
        augmented = f"{product} material:{material}"

    scored: list[tuple[float, ClassificationAlternative]] = []
    for hs6, desc, signals in _HS_CANDIDATES:
        alt = _candidate_from_hs6(hs6, augmented, signals)
        if alt.confidence > 0.0:
            scored.append((alt.confidence, alt))

    scored.sort(key=lambda x: -x[0])

    if not alternatives:
        # If no known HS, top match is first
        for _, alt in scored[:8]:
            alternatives.append(alt)
    else:
        # Merge known-hs candidate with top attribute matches
        top_score = scored[0][0] if scored else 0.0
        for _, alt in scored[:8]:
            alternatives.append(alt)

    # Deduplicate and reorder: known HS first if present
    if known_hs_code and validate_hs_code(known_hs_code):
        known_alt = next(
            (a for a in alternatives if a.code == known_hs_code), None
        )
        if known_alt:
            alternatives.remove(known_alt)
            alternatives.insert(0, known_alt)

    return alternatives


def build_classification(
    product: str,
    material: Optional[str],
    known_hs_code: Optional[str],
    origin_country: str,
    destination_country: str,
    evidence_ledger: list[Evidence],
) -> Classification:
    """Build the Classification block for the assessment.

    V0 deliberately does NOT attempt a full ML classifier.  It uses:
      - known_hs_code validation + enrichment (CN/TARIC stubs)
      - keyword/attribute scoring against curated HS candidate list
      - explicit review_required when confidence is low or ambiguous
    """
    alts = extract_classification_candidates(product, material, known_hs_code)

    if not alts:
        return Classification(
            hs_code=None,
            cn_code=None,
            taric_code=None,
            description=None,
            confidence=0.0,
            alternatives=[],
            reasoning_summary="No classification candidates produced — product description too ambiguous.",
            review_required=True,
            evidence=[],
        )

    primary = alts[0]
    confidence = primary.confidence

    # Determine the level of the primary candidate
    primary_level = primary.level or "hs"

    hs_code = None
    cn_code = None
    taric_code = None
    description = None
    reasoning_parts: list[str] = []

    if primary_level == "hs":
        hs_code = primary.code
        # In V0 we do NOT fabricate CN/TARIC codes.  We note that the
        # 8/10-digit extensions are determined at import by the national
        # customs authority via TARIC / national nomenclature.
        cn_code = None
        taric_code = None
        description = _hs_description_from_code(hs_code) or None
        reasoning_parts.append(
            f"Primary classification: HS {hs_code} ({description or 'no description available'})."
        )
        if confidence < 0.5:
            reasoning_parts.append(
                "Low confidence — classification is ambiguous; see alternatives."
            )
        else:
            reasoning_parts.append(
                f"Confidence {round(confidence, 2)} based on keyword/attribute match."
            )

    elif primary_level == "cn":
        # When caller supplies an 8-digit code, treat it as CN
        cn_code = primary.code
        hs_code = cn_code[:6] if len(cn_code) >= 6 else cn_code
        description = _hs_description_from_code(hs_code) or None
        reasoning_parts.append(
            f"Caller-supplied CN code {cn_code} validated. "
            f"HS parent: {hs_code}."
        )
        if len(cn_code) == 10:
            taric_code = cn_code
            reasoning_parts.append(
                "Code is 10 digits — interpreted as TARIC for V0 without external TARIC lookup."
            )

    elif primary_level == "taric":
        taric_code = primary.code
        cn_code = primary.code[:8] if len(primary.code) >= 8 else primary.code
        hs_code = cn_code[:6] if len(cn_code) >= 6 else cn_code
        description = _hs_description_from_code(hs_code) or None
        reasoning_parts.append(
            f"Caller-supplied TARIC code {primary.code} validated."
        )

    else:
        # Fallback for any unexpected level
        hs_code = primary.code
        description = _hs_description_from_code(primary.code) or None
        reasoning_parts.append(f"Primary candidate: {primary.code} ({description or 'no description'}).")

    reasoning_parts.append(
        "V0 classification is based on keyword/attribute matching and candidate scoring. "
        "It is NOT legally binding and must NOT be used for customs filing without review."
    )

    if not known_hs_code:
        reasoning_parts.append(
            "No HS code supplied by caller — classification is inferred from product description "
            "and material attributes only."
        )

    review_required = confidence < 0.5 or (
        known_hs_code is None and len(alts) > 1 and alts[1].confidence > 0.3
    )

    return Classification(
        hs_code=hs_code,
        cn_code=cn_code,
        taric_code=taric_code,
        description=description or "No description available from V0 reference data.",
        confidence=round(confidence, 2),
        alternatives=alts[1:5] if len(alts) > 1 else [],
        reasoning_summary=" ".join(reasoning_parts),
        review_required=review_required,
        evidence=[],
    )


def _hs_description_from_code(hs6: str) -> Optional[str]:
    """Return the WCO HS 2022 heading description for a 6-digit code (V0 curated)."""
    # Curated 6-digit heading descriptions for V0 candidate codes.
    # These are summary labels, not full legal text.
    descriptions: dict[str, str] = {
        "9617": "Vacuum flasks and insulated containers (WCO HS 2022 heading 9617).",
        "9619": "Hot water bottles, bag, ...",
        "9618": "Massage tables.",
        "9621": "Breathing appliances.",
        "4202": "Trunks, suitcases, vanity cases, briefcases, bags, wallets, pouches,",
        "6202": "Women's suits, jackets, dresses, skirts, trousers, shorts (woven),",
        "6203": "Men's or boys' suits, jackets, blazers, trousers,",
        "6204": "Women's or girls' suits, jackets, dresses, skirts,",
        "6205": "Men's or boys' shirts (woven),",
        "6206": "Women's or girls' blouses, shirts (woven),",
        "6306": "Cheesecloth, gauze, canvas, tarpaulins,",
        "7307": "Tubes and pipes of iron or steel,",
        "7308": "Structures and parts of structures of iron or steel,",
        "8301": "Steel swords, knives, daggers, razors,",
        "8413": "Pumps for liquids, piston pumps,",
        "8415": "Refrigerating equipment, refrigerators,",
        "8418": "Refrigerators, freezers, cold stores,",
        "8421": "Centrifuges, filtering equipment,",
        "8423": "Weighing machinery, balances,",
        "8450": "Household washing machines, dryers,",
        "8456": "Tools for drilling, cutting, grinding,",
        "8466": "Parts for machine tools,",
        "8479": "Machines with individual functions,",
        "8481": "Taps, cocks, valves,",
        "8482": "Ball bearings,",
        "8483": "Transmission shafts, cranks, gears,",
        "8506": "Primary cells and batteries,",
        "8507": "Electric accumulators,",
        "8509": "Electro-mechanical domestic appliances,",
        "8516": "Electric heating,",
        "8517": "Telephone sets, smartphones,",
        "8518": "Microphones, loudspeakers,",
        "8523": "Recording media,",
        "8525": "Transmission apparatus,",
        "8528": "Monitors, projectors,",
        "8529": "Parts for television/radio,",
        "8531": "Signaling equipment,",
        "8536": "Electrical switching apparatus, fuses,",
        "8537": "Control panels, boards,",
        "8542": "Electronic integrated circuits,",
        "8543": "Electrical machines and apparatus,",
        "8544": "Insulated wire, cable,",
        "8708": "Parts for motor vehicles,",
        "8715": "Baby transport equipment,",
        "9401": "Seats (other than medical),",
        "9403": "Other furniture and parts,",
        "9404": "Mattresses,",
        "9405": "Lamps and lighting accessories,",
        "9503": "Tricycles, toy vehicles,",
        "9504": "Video game consoles,",
        "9508": "Circus and funfair equipment,",
        "9706": "Antiques.",
    }
    return descriptions.get(hs6)


def add_classification_evidence(
    classification: Classification,
    hs_code: Optional[str],
    evidence_ledger: list[Evidence],
) -> None:
    """Attach provenance evidence to the classification block.

    V0: We attach one Evidence record describing the classification approach
    (keyword/attribute matching, no live HS codebook lookup).  The source is
    'factrail internal classification engine' — factually accurate for V0.
    """
    if not hs_code:
        return

    ev = Evidence(
        value=f"HS {hs_code}",
        status=ProvenanceStatus.INFERRED,
        authority="FACTRAIL classification engine (V0)",
        source="internal keyword/attribute classifier",
        url="https://factrail.online/trade/classification",
        retrieved_at=datetime.now(timezone.utc),
        effective_date=None,
        supports=["classification"],
        confidence=0.5,
        note="V0 uses curated keyword/attribute matching against HS 2022 heading summaries. "
             "No live HS codebook, TARIC, or BTI data integrated yet.",
    )
    classification.evidence.append(ev)
    evidence_ledger.append(ev)
