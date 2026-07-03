"""CRM reads the plan catalog from dotmac_sub (GET /crm/offers)."""


def test_fetch_offers_returns_catalog(db_session, monkeypatch):
    from app.services import selfcare

    seen = {}

    def _fake(db, method, path, *, params=None, json_body=None):
        seen["path"] = path
        seen["params"] = params
        return {"data": [{"id": "o1", "code": "HOME100", "recurring_price": "15000.00", "billing_cycle": "monthly"}]}

    monkeypatch.setattr(selfcare, "_request_json", _fake)
    offers = selfcare.fetch_offers(db_session, q="home")
    assert offers[0]["code"] == "HOME100"
    assert seen["path"] == "/offers"
    assert seen["params"]["q"] == "home"
    assert seen["params"]["active_only"] == "true"


def test_fetch_offers_empty_on_bad_payload(db_session, monkeypatch):
    from app.services import selfcare

    monkeypatch.setattr(selfcare, "_request_json", lambda *a, **k: {"data": "nope"})
    assert selfcare.fetch_offers(db_session) == []
