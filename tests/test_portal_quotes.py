"""Self-serve quotes: feasibility classification, estimate/deposit math, payload."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.crm import portal_quotes

_CFG = {
    "enabled": True,
    "base_fee": Decimal("50000.00"),
    "free_radius_m": 300,
    "fee_per_km": Decimal("25000.00"),
    "deposit_percent": 50,
    "feasibility_radius_m": 2000,
}


def _patch_settings(monkeypatch):
    monkeypatch.setattr(portal_quotes, "_settings", lambda db: dict(_CFG))


def test_feasibility_covered_within_radius(monkeypatch):
    _patch_settings(monkeypatch)
    fap = SimpleNamespace(id="fap-1", name="FAP Wuse")
    monkeypatch.setattr(portal_quotes, "_nearest_fiber_access_point", lambda db, lat, lng: (fap, 800.0))
    out = portal_quotes.compute_feasibility(None, 9.07, 7.49)
    assert out["coverage"] == "covered"
    assert out["feasible"] is True
    assert out["nearest_fap_name"] == "FAP Wuse"
    assert out["distance_meters"] == 800.0


def test_feasibility_survey_required_beyond_radius(monkeypatch):
    _patch_settings(monkeypatch)
    fap = SimpleNamespace(id="fap-1", name="FAP Far")
    monkeypatch.setattr(portal_quotes, "_nearest_fiber_access_point", lambda db, lat, lng: (fap, 5000.0))
    out = portal_quotes.compute_feasibility(None, 9.07, 7.49)
    assert out["coverage"] == "survey_required"
    assert out["feasible"] is True


def test_feasibility_out_of_area_when_no_plant(monkeypatch):
    _patch_settings(monkeypatch)
    monkeypatch.setattr(portal_quotes, "_nearest_fiber_access_point", lambda db, lat, lng: (None, None))
    out = portal_quotes.compute_feasibility(None, 9.07, 7.49)
    assert out["coverage"] == "out_of_area"
    assert out["feasible"] is False
    assert out["nearest_fap_id"] is None


def test_estimate_covered_adds_distance_surcharge(monkeypatch):
    _patch_settings(monkeypatch)
    feasibility = {"coverage": "covered", "distance_meters": 1300.0}
    est = portal_quotes.compute_estimate(None, feasibility, "NGN")
    # 1300m - 300m free = 1000m = 1km * 25000 = 25000 surcharge.
    assert est["base_fee"] == Decimal("50000.00")
    assert est["distance_fee"] == Decimal("25000.00")
    assert est["subtotal"] == Decimal("75000.00")
    assert est["deposit_amount"] == Decimal("37500.00")  # 50%
    assert est["provisional"] is False
    assert len(est["line_items"]) == 2


def test_estimate_covered_within_free_radius_has_no_surcharge(monkeypatch):
    _patch_settings(monkeypatch)
    est = portal_quotes.compute_estimate(None, {"coverage": "covered", "distance_meters": 200.0}, "NGN")
    assert est["distance_fee"] == Decimal("0.00")
    assert est["subtotal"] == Decimal("50000.00")
    assert len(est["line_items"]) == 1


def test_estimate_out_of_area_is_provisional_base_only(monkeypatch):
    _patch_settings(monkeypatch)
    est = portal_quotes.compute_estimate(None, {"coverage": "out_of_area", "distance_meters": None}, "NGN")
    assert est["provisional"] is True
    assert est["distance_fee"] == Decimal("0.00")
    assert est["subtotal"] == Decimal("50000.00")


def test_build_payload_serializes_quote(monkeypatch):
    monkeypatch.setattr(portal_quotes, "_find_existing_project_for_quote", lambda db, qid: None)
    li = SimpleNamespace(
        description="Fiber installation (base)",
        quantity=Decimal("1.000"),
        unit_price=Decimal("50000.00"),
        amount=Decimal("50000.00"),
        created_at=None,
        id="li1",
    )
    quote = SimpleNamespace(
        id="q1",
        status=SimpleNamespace(value="draft"),
        currency="NGN",
        subtotal=Decimal("50000.00"),
        tax_total=Decimal("0.00"),
        total=Decimal("50000.00"),
        line_items=[li],
        created_at=None,
        expires_at=None,
        metadata_={
            "source": "portal_self_serve",
            "project_type": "fiber_optics_installation",
            "subscriber_id": "s1",
            "subscriber_external_id": "ext-1",
            "install": {"latitude": 9.07, "longitude": 7.49, "address": "Wuse", "region": "FCT"},
            "feasibility": {
                "coverage": "covered",
                "feasible": True,
                "distance_meters": 800.0,
                "nearest_fap_name": "FAP",
            },
            "deposit_percent": 50,
            "estimate_provisional": False,
        },
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None  # no sales order yet

    out = portal_quotes.build_portal_quote_payload(db, quote)
    assert out["id"] == "q1"
    assert out["status"] == "draft"
    assert out["subscriber_external_id"] == "ext-1"
    assert out["latitude"] == 9.07
    assert out["feasibility"]["coverage"] == "covered"
    assert out["deposit_percent"] == 50
    assert out["deposit_amount"] == "25000.00"  # 50% of 50000
    assert out["deposit_paid"] is False
    assert out["sales_order_id"] is None
    assert out["project_id"] is None
    assert len(out["line_items"]) == 1
    assert out["line_items"][0]["unit_price"] == "50000.00"
