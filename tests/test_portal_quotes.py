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
    "bundle_sku": None,
    "base_sku": None,
    "distance_sku": None,
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


# --- Catalog-backed pricing (price book) + flat bundle ----------------------

from app.models.inventory import InventoryItem  # noqa: E402


def _item(db, sku, name, price):
    it = InventoryItem(sku=sku, name=name, unit_price=Decimal(price), currency="NGN", is_active=True)
    db.add(it)
    db.commit()
    db.refresh(it)
    return it


def _cfg(**over):
    c = dict(_CFG)
    c.update(over)
    return c


def test_bundle_mode_is_flat_irrespective_of_distance(db_session, monkeypatch):
    bundle = _item(db_session, "INSTALL-BUNDLE", "Standard Fibre Install", "120000.00")
    monkeypatch.setattr(portal_quotes, "_settings", lambda db: _cfg(bundle_sku="INSTALL-BUNDLE"))
    # A far location (would add a big distance surcharge in derived mode).
    est = portal_quotes.compute_estimate(db_session, {"coverage": "covered", "distance_meters": 5000.0}, "NGN")
    assert est["pricing_mode"] == "bundle"
    assert est["subtotal"] == Decimal("120000.00")  # flat, distance ignored
    assert est["distance_fee"] == Decimal("0.00")
    assert est["deposit_amount"] == Decimal("60000.00")
    assert len(est["line_items"]) == 1
    assert est["line_items"][0]["inventory_item_id"] == bundle.id


def test_derived_mode_sources_prices_from_catalog(db_session, monkeypatch):
    base = _item(db_session, "INSTALL-BASE", "Base install", "40000.00")
    drop = _item(db_session, "DROP-PER-KM", "Drop cable", "10000.00")
    monkeypatch.setattr(
        portal_quotes, "_settings", lambda db: _cfg(base_sku="INSTALL-BASE", distance_sku="DROP-PER-KM")
    )
    est = portal_quotes.compute_estimate(db_session, {"coverage": "covered", "distance_meters": 1300.0}, "NGN")
    assert est["pricing_mode"] == "derived"
    assert est["base_fee"] == Decimal("40000.00")  # from catalog, not the 50000 setting
    assert est["distance_fee"] == Decimal("10000.00")  # 1km beyond free radius * 10000
    assert est["subtotal"] == Decimal("50000.00")
    assert est["line_items"][0]["inventory_item_id"] == base.id
    assert est["line_items"][1]["inventory_item_id"] == drop.id


def test_derived_mode_falls_back_to_settings_when_no_catalog_item(db_session, monkeypatch):
    monkeypatch.setattr(portal_quotes, "_settings", lambda db: _cfg(base_sku="DOES-NOT-EXIST"))
    est = portal_quotes.compute_estimate(db_session, {"coverage": "covered", "distance_meters": 200.0}, "NGN")
    assert est["pricing_mode"] == "derived"
    assert est["base_fee"] == Decimal("50000.00")  # settings fallback
    assert est["line_items"][0]["inventory_item_id"] is None


def test_request_creates_lead_with_portal_source(db_session, monkeypatch, person):
    """Regression: the self-serve quote request creates its lead with
    lead_source="portal", which _normalize_lead_source_or_400 used to 400
    ("Invalid lead_source") because "Portal" was missing from the vocabulary —
    breaking the portal quote-request path end-to-end."""
    from types import SimpleNamespace as _NS
    from uuid import uuid4

    from app.models.crm.sales import Lead
    from app.services.crm import portal_scope
    from app.services.portal_auth import PortalPrincipal

    monkeypatch.setattr(portal_quotes, "_settings", lambda db: dict(_CFG))
    fap = _NS(id="fap-1", name="FAP Wuse")
    monkeypatch.setattr(portal_quotes, "_nearest_fiber_access_point", lambda db, lat, lng: (fap, 200.0))
    monkeypatch.setattr(portal_quotes, "_emit_quote_to_sub", lambda db, quote, event_type: None)
    subscriber = _NS(id=uuid4(), external_id="EXT-1", person_id=person.id)
    monkeypatch.setattr(portal_scope, "resolve_target_subscriber", lambda db, principal, sid: subscriber)

    principal = PortalPrincipal(subject_id=str(subscriber.id), actor="subscriber", scopes=["quotes:write"])
    quote = portal_quotes.PortalQuotes.request(
        db_session,
        principal,
        latitude=9.07,
        longitude=7.49,
        address="1 Test Street",
        region="Abuja",
        note="self-serve",
    )

    assert quote.person_id == person.id
    lead = db_session.get(Lead, quote.lead_id)
    assert lead is not None
    assert lead.lead_source == "Portal"  # normalized canonical value
    assert lead.metadata_["source"] == "portal_self_serve"


def test_lead_source_portal_is_normalized_and_unknowns_still_400():
    import pytest
    from fastapi import HTTPException

    from app.services.crm.sales.service import _normalize_lead_source_or_400

    assert _normalize_lead_source_or_400("portal") == "Portal"
    assert _normalize_lead_source_or_400("Portal") == "Portal"
    with pytest.raises(HTTPException) as exc:
        _normalize_lead_source_or_400("carrier-pigeon")
    assert exc.value.status_code == 400
