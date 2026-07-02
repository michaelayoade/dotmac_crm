"""The plan picker reads the dotmac_sub offer catalog via /api/search/catalog-offers."""

from app.api import search as search_api


def test_catalog_offers_maps_sub_offers(db_session, monkeypatch):
    from app.services import selfcare

    monkeypatch.setattr(selfcare, "is_customer_sync_enabled", lambda db: True)
    monkeypatch.setattr(
        selfcare,
        "fetch_offers",
        lambda db, *, q=None, active_only=True: [
            {
                "id": "o-1",
                "code": "HOME100",
                "name": "Home 100M",
                "recurring_price": "15000.00",
                "currency": "NGN",
                "billing_cycle": "monthly",
                "speed_download_mbps": 100,
            }
        ],
    )

    resp = search_api.search_catalog_offers(q="home", limit=20, db=db_session)
    assert resp["count"] == 1
    item = resp["items"][0]
    assert item.id == "o-1"
    assert item.code == "HOME100"
    assert item.label == "HOME100 — Home 100M"
    assert item.recurring_price == "15000.00"
    assert item.billing_cycle == "monthly"
    assert item.speed_download_mbps == 100


def test_catalog_offers_empty_when_sync_disabled(db_session, monkeypatch):
    from app.services import selfcare

    monkeypatch.setattr(selfcare, "is_customer_sync_enabled", lambda db: False)

    def _boom(*a, **k):
        raise AssertionError("must not call sub when sync is off")

    monkeypatch.setattr(selfcare, "fetch_offers", _boom)
    resp = search_api.search_catalog_offers(q="x", limit=20, db=db_session)
    assert resp["items"] == [] and resp["count"] == 0


def test_catalog_offers_empty_on_upstream_error(db_session, monkeypatch):
    from app.services import selfcare

    monkeypatch.setattr(selfcare, "is_customer_sync_enabled", lambda db: True)

    def _boom(*a, **k):
        raise RuntimeError("sub down")

    monkeypatch.setattr(selfcare, "fetch_offers", _boom)
    resp = search_api.search_catalog_offers(q="x", limit=20, db=db_session)
    assert resp["items"] == []
