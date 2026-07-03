"""CRM creates a subscription in dotmac_sub from a sale (POST /crm/subscriptions)."""


def test_create_subscription_posts_offer_and_ref(db_session, monkeypatch):
    from app.services import selfcare

    seen = {}

    def _fake(db, method, path, *, params=None, json_body=None):
        seen["method"] = method
        seen["path"] = path
        seen["body"] = json_body
        return {"data": {"subscription_id": "sub-1", "invoice_id": "inv-1", "status": "pending", "created": True}}

    monkeypatch.setattr(selfcare, "_request_json", _fake)
    result = selfcare.create_subscription(
        db_session,
        subscriber_id="cust-9",
        offer_ref="HOME100",
        external_ref="sales_order:42:subscription:7",
        unit_price="15000.00",
    )
    assert result is not None
    assert result["subscription_id"] == "sub-1"
    assert result["created"] is True
    assert seen["method"] == "POST"
    assert seen["path"] == "/subscriptions"
    assert seen["body"]["subscriber_id"] == "cust-9"
    assert seen["body"]["offer_ref"] == "HOME100"
    assert seen["body"]["external_ref"] == "sales_order:42:subscription:7"
    assert seen["body"]["unit_price"] == "15000.00"


def test_create_subscription_requires_core_fields(db_session, monkeypatch):
    from app.services import selfcare

    # Should never hit the network when a required field is missing.
    def _boom(*a, **k):
        raise AssertionError("must not call the API")

    monkeypatch.setattr(selfcare, "_request_json", _boom)
    assert selfcare.create_subscription(db_session, subscriber_id="", offer_ref="X", external_ref="r") is None
    assert selfcare.create_subscription(db_session, subscriber_id="c", offer_ref="", external_ref="r") is None
    assert selfcare.create_subscription(db_session, subscriber_id="c", offer_ref="X", external_ref="") is None


def test_create_subscription_none_on_bad_payload(db_session, monkeypatch):
    from app.services import selfcare

    monkeypatch.setattr(selfcare, "_request_json", lambda *a, **k: {"data": "nope"})
    result = selfcare.create_subscription(db_session, subscriber_id="c", offer_ref="X", external_ref="r")
    assert result is None
