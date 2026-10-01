from urbex_crm.buckets import classify, detect_brands, needs_llm
from urbex_crm.models import Sector, Tier


def test_premium_dealer_is_high():
    c = classify("Autos Premium Madrid", "Concesionario oficial Mercedes-Benz", brands="Mercedes-Benz, Smart")
    assert c.sector == Sector.AUTOMOTIVE_DEALER
    assert c.tier == Tier.HIGH
    assert "mercedes" in c.reason


def test_budget_used_lot_is_low():
    c = classify("Ocasión Rápida", "Compra venta de coches de segunda mano multimarca")
    assert c.sector == Sector.AUTOMOTIVE_DEALER
    assert c.sub_sector == "used_cars"
    assert c.tier == Tier.LOW


def test_mainstream_brand_with_big_revenue_goes_high():
    c = classify("Motor Centro", "Concesionario Seat", brands="Seat", est_revenue_eur=25_000_000)
    assert c.tier == Tier.HIGH


def test_construction_by_size():
    big = classify("Construcciones Levante SA", "Constructora de obra civil e infraestructuras", employee_count=310)
    small = classify("Reformas Pérez", "Reformas integrales y rehabilitación", employee_count=9)
    assert big.sector == Sector.CONSTRUCTION and big.sub_sector == "civil_works" and big.tier == Tier.HIGH
    assert small.sub_sector == "renovation" and small.tier == Tier.LOW


def test_developer_beats_generic_construction():
    c = classify("Promociones Norte", "Promotora inmobiliaria de vivienda nueva", est_revenue_eur=15e6)
    assert c.sector == Sector.CONSTRUCTION_DEVELOPER
    assert c.tier == Tier.MID


def test_unknown_needs_llm():
    c = classify("Acme Holdings", "")
    assert c.sector == Sector.OTHER and c.tier == Tier.UNKNOWN and needs_llm(c)


def test_detect_brands_word_boundaries():
    assert "mg" not in detect_brands("omega consulting")
    assert "bmw" in detect_brands("BMW Motorrad Madrid")
