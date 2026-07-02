"""CRM → sub subscription sync: create a sub subscription from tagged sales-order lines."""

from types import SimpleNamespace

from app.models.sales_order import SalesOrderPaymentStatus
from app.services import sales_orders
from app.services.events.handlers import selfcare_customer


def test_line_offer_ref_reads_metadata():
    assert selfcare_customer._line_offer_ref(SimpleNamespace(metadata_={"sub_offer_id": "o-1"})) == "o-1"
    assert selfcare_customer._line_offer_ref(SimpleNamespace(metadata_={"sub_offer_code": "HOME100"})) == "HOME100"
    assert selfcare_customer._line_offer_ref(SimpleNamespace(metadata_={"offer_id": "o-2"})) == "o-2"
    # installation / untagged lines are not subscriptions
    assert selfcare_customer._line_offer_ref(SimpleNamespace(metadata_={"foo": "bar"})) is None
    assert selfcare_customer._line_offer_ref(SimpleNamespace(metadata_=None)) is None


def test_trigger_fires_both_pushes_on_paid_order(db_session, monkeypatch):
    import app.services.events.handlers.selfcare_customer as handler

    pay, subs = [], []
    monkeypatch.setattr(handler, "push_sales_order_payment_to_selfcare", lambda db, so: pay.append(so))
    monkeypatch.setattr(handler, "push_sales_order_subscription_to_selfcare", lambda db, so: subs.append(so))

    paid = SimpleNamespace(payment_status=SalesOrderPaymentStatus.paid)
    sales_orders._sync_sales_order_payment_to_sub(db_session, paid)
    assert pay == [paid] and subs == [paid]


def test_subscription_push_skips_when_sync_disabled(db_session, monkeypatch):
    from app.services import selfcare

    monkeypatch.setattr(selfcare, "is_customer_sync_enabled", lambda db: False)
    called = []
    monkeypatch.setattr(selfcare, "create_subscription", lambda *a, **k: called.append(k) or {})

    selfcare_customer.push_sales_order_subscription_to_selfcare(db_session, SimpleNamespace(id="1"))
    assert called == []


def test_subscription_push_creates_and_tags_line(db_session, monkeypatch):
    from app.services import selfcare

    line = SimpleNamespace(id="ln-1", unit_price="15000", metadata_={"sub_offer_id": "HOME100"})
    sales_order = SimpleNamespace(id="so-9")

    monkeypatch.setattr(selfcare, "is_customer_sync_enabled", lambda db: True)
    monkeypatch.setattr(selfcare_customer, "_sales_order_lines", lambda db, soid: [line])
    monkeypatch.setattr(
        selfcare_customer, "_resolve_project_for_sales_order", lambda db, soid: SimpleNamespace(id="p-1")
    )
    monkeypatch.setattr(selfcare_customer, "_resolve_person_for_project", lambda db, p: SimpleNamespace())
    monkeypatch.setattr(selfcare_customer, "_selfcare_identity", lambda p: SimpleNamespace(external_id="cust-3"))

    sent = {}

    def _create(db, *, subscriber_id, offer_ref, external_ref, unit_price=None):
        sent.update(subscriber_id=subscriber_id, offer_ref=offer_ref, external_ref=external_ref, unit_price=unit_price)
        return {"subscription_id": "sub-1", "invoice_id": "inv-1", "created": True}

    monkeypatch.setattr(selfcare, "create_subscription", _create)

    selfcare_customer.push_sales_order_subscription_to_selfcare(db_session, sales_order)

    assert sent["subscriber_id"] == "cust-3"
    assert sent["offer_ref"] == "HOME100"
    assert sent["external_ref"] == "sales_order:so-9:subscription:ln-1"
    assert line.metadata_["selfcare_subscription_id"] == "sub-1"
    assert line.metadata_["selfcare_subscription_invoice_id"] == "inv-1"


def test_subscription_push_idempotent_skips_already_synced(db_session, monkeypatch):
    from app.services import selfcare

    line = SimpleNamespace(
        id="ln-1", unit_price="15000", metadata_={"sub_offer_id": "HOME100", "selfcare_subscription_id": "sub-existing"}
    )
    monkeypatch.setattr(selfcare, "is_customer_sync_enabled", lambda db: True)
    monkeypatch.setattr(selfcare_customer, "_sales_order_lines", lambda db, soid: [line])
    monkeypatch.setattr(
        selfcare_customer, "_resolve_project_for_sales_order", lambda db, soid: SimpleNamespace(id="p-1")
    )
    monkeypatch.setattr(selfcare_customer, "_resolve_person_for_project", lambda db, p: SimpleNamespace())
    monkeypatch.setattr(selfcare_customer, "_selfcare_identity", lambda p: SimpleNamespace(external_id="cust-3"))

    called = []
    monkeypatch.setattr(selfcare, "create_subscription", lambda *a, **k: called.append(k) or {})

    selfcare_customer.push_sales_order_subscription_to_selfcare(db_session, SimpleNamespace(id="so-9"))
    assert called == []  # already synced → no second create
