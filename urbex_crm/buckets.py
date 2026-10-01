"""Sector + income-tier bucketing.

Deterministic rules first (cheap, explainable); the LLM only resolves what the
rules can't. Every classification returns a reason string so you can audit it
from Telegram.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Sector, Tier

PREMIUM_BRANDS = {
    "mercedes", "mercedes-benz", "bmw", "audi", "porsche", "lexus", "tesla", "jaguar",
    "land rover", "range rover", "volvo", "maserati", "ferrari", "lamborghini", "bentley",
    "rolls-royce", "aston martin", "mini", "ds", "genesis", "cupra", "alfa romeo", "polestar",
}
MAINSTREAM_BRANDS = {
    "volkswagen", "vw", "toyota", "ford", "seat", "renault", "peugeot", "kia", "hyundai",
    "nissan", "opel", "citroen", "citroën", "fiat", "skoda", "škoda", "honda", "mazda",
    "mg", "byd", "jeep", "suzuki", "subaru", "mitsubishi",
}
BUDGET_BRANDS = {"dacia", "lada", "chery", "omoda", "jaecoo", "ssangyong", "kgm"}

SECTOR_KEYWORDS: dict[Sector, list[str]] = {
    Sector.AUTOMOTIVE_DEALER: [
        "concesionario", "concesionarios", "dealer", "dealership", "automoviles", "automóviles",
        "motor", "autos", "coches", "vehiculos", "vehículos", "km0", "km 0", "ocasion", "ocasión",
        "seminuevos", "car sales",
    ],
    Sector.AUTOMOTIVE_SERVICES: [
        "taller", "talleres", "rent a car", "alquiler de coches", "renting", "flota", "fleet",
        "recambios", "neumaticos", "neumáticos", "chapa y pintura", "itv", "auto repair",
    ],
    Sector.CONSTRUCTION_DEVELOPER: [
        "promotora", "promociones", "promotor", "developer", "real estate development", "inmobiliaria",
    ],
    Sector.CONSTRUCTION_MATERIALS: [
        "materiales de construccion", "materiales de construcción", "cementos", "hormigon", "hormigón",
        "prefabricados", "aridos", "áridos", "building materials", "ferreteria", "ferretería",
    ],
    Sector.CONSTRUCTION: [
        "constructora", "construcciones", "construccion", "construcción", "obras", "reformas",
        "edificacion", "edificación", "contratista", "contractor", "ingenieria civil", "ingeniería civil",
        "infraestructuras", "urbanizacion", "urbanización", "rehabilitacion", "rehabilitación",
        "builder", "construction",
    ],
}

# Size thresholds used when brands don't decide (construction, services).
REVENUE_HIGH_EUR = 20_000_000
REVENUE_MID_EUR = 3_000_000
EMPLOYEES_HIGH = 100
EMPLOYEES_MID = 20


@dataclass
class Classification:
    sector: Sector
    sub_sector: str | None
    tier: Tier
    confidence: float
    reason: str


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def detect_brands(text: str | None) -> list[str]:
    t = _norm(text)
    found = []
    for brand in sorted(PREMIUM_BRANDS | MAINSTREAM_BRANDS | BUDGET_BRANDS, key=len, reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(brand)}(?![a-z])", t):
            found.append(brand)
    return found


def classify_sector(name: str, description: str | None = None, website: str | None = None) -> tuple[Sector, str | None, float]:
    blob = _norm(" ".join(p for p in [name, description, website] if p))
    scores: dict[Sector, int] = {}
    for sector, words in SECTOR_KEYWORDS.items():
        hits = sum(1 for w in words if w in blob)
        if hits:
            scores[sector] = hits
    if detect_brands(blob) and Sector.AUTOMOTIVE_DEALER not in scores:
        scores[Sector.AUTOMOTIVE_DEALER] = 1
    if not scores:
        return Sector.OTHER, None, 0.0
    # Specific sectors beat the generic "construction" bucket on ties.
    priority = [Sector.CONSTRUCTION_DEVELOPER, Sector.CONSTRUCTION_MATERIALS, Sector.AUTOMOTIVE_SERVICES,
                Sector.AUTOMOTIVE_DEALER, Sector.CONSTRUCTION]
    best = max(scores, key=lambda s: (scores[s], -priority.index(s)))
    sub = None
    if best == Sector.AUTOMOTIVE_DEALER:
        if any(k in blob for k in ("ocasion", "ocasión", "seminuevos", "km0", "km 0")):
            sub = "used_cars"
        elif detect_brands(blob):
            sub = "brand_dealer"
        else:
            sub = "multi_brand"
    elif best == Sector.CONSTRUCTION:
        if any(k in blob for k in ("reformas", "rehabilitacion", "rehabilitación")):
            sub = "renovation"
        elif any(k in blob for k in ("infraestructuras", "ingenieria civil", "ingeniería civil", "obra civil")):
            sub = "civil_works"
        else:
            sub = "general_contractor"
    conf = min(1.0, 0.5 + 0.15 * scores[best])
    return best, sub, conf


def classify_tier(
    sector: Sector,
    brands: str | None = None,
    name: str | None = None,
    description: str | None = None,
    employee_count: int | None = None,
    est_revenue_eur: float | None = None,
) -> tuple[Tier, float, str]:
    reasons: list[str] = []

    # 1) Automotive: brand portfolio is the strongest income signal.
    if sector in (Sector.AUTOMOTIVE_DEALER, Sector.AUTOMOTIVE_SERVICES):
        found = detect_brands(" ".join(p for p in [brands, name, description] if p))
        if found:
            if any(b in PREMIUM_BRANDS for b in found):
                return Tier.HIGH, 0.9, f"premium brand(s): {', '.join(b for b in found if b in PREMIUM_BRANDS)}"
            if all(b in BUDGET_BRANDS for b in found):
                return Tier.LOW, 0.8, f"budget brand(s): {', '.join(found)}"
            reasons.append(f"mainstream brand(s): {', '.join(found)}")
            tier_from_brand = Tier.MID
        else:
            tier_from_brand = None
            blob = _norm(" ".join(p for p in [name, description] if p))
            if any(k in blob for k in ("ocasion", "ocasión", "seminuevos", "segunda mano", "multimarca")):
                reasons.append("used / multi-brand lot")
                tier_from_brand = Tier.LOW
    else:
        tier_from_brand = None

    # 2) Size signals (revenue beats headcount).
    size_tier = None
    if est_revenue_eur is not None:
        if est_revenue_eur >= REVENUE_HIGH_EUR:
            size_tier = Tier.HIGH
        elif est_revenue_eur >= REVENUE_MID_EUR:
            size_tier = Tier.MID
        else:
            size_tier = Tier.LOW
        reasons.append(f"revenue ~{est_revenue_eur/1e6:.1f}M EUR")
    elif employee_count is not None:
        if employee_count >= EMPLOYEES_HIGH:
            size_tier = Tier.HIGH
        elif employee_count >= EMPLOYEES_MID:
            size_tier = Tier.MID
        else:
            size_tier = Tier.LOW
        reasons.append(f"{employee_count} employees")

    if tier_from_brand and size_tier:
        order = [Tier.LOW, Tier.MID, Tier.HIGH]
        tier = order[max(order.index(tier_from_brand), order.index(size_tier))]
        return tier, 0.85, "; ".join(reasons)
    if size_tier:
        return size_tier, 0.7, "; ".join(reasons)
    if tier_from_brand:
        return tier_from_brand, 0.6, "; ".join(reasons)
    return Tier.UNKNOWN, 0.0, "no brand or size signal"


def classify(
    name: str,
    description: str | None = None,
    website: str | None = None,
    brands: str | None = None,
    employee_count: int | None = None,
    est_revenue_eur: float | None = None,
) -> Classification:
    sector, sub, s_conf = classify_sector(name, description, website)
    tier, t_conf, reason = classify_tier(sector, brands, name, description, employee_count, est_revenue_eur)
    return Classification(sector, sub, tier, round(min(s_conf, t_conf) if s_conf else t_conf, 2), reason)


def needs_llm(c: Classification) -> bool:
    return c.sector == Sector.OTHER or c.tier == Tier.UNKNOWN or c.confidence < 0.6
