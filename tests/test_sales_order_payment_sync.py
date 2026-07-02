"""CRM → sub payment sync: selfcare.record_payment + the SalesOrder-paid trigger."""

from types import SimpleNamespace

from app.models.sales_order import SalesOrderPaymentStatus
from app.services import sales_orders


def test_record_payment_posts_to_payments_endpoint(db_session, monkeypatch):
    from app.services import selfcare

    seen = {}

    def _fake(db, method, path, *, params=None, json_body=None):
        seen["method"] = method
        seen["path"] = path
        seen["body"] = json_body
        return {"data": {"id": "pay-1"}}

    monkeypatch.setattr(selfcare, "_request_json", _fake)
    out = selfcare.record_payment(
        db_session,
        subscriber_id="sub-1",
        amount="5000",
        external_ref="sales_order:1:payment",
        invoice_external_ref="project:9",
    )
    assert out == "pay-1"
    assert (seen["method"], seen["path"]) == ("POST", "/payments")
    assert seen["body"]["subscriber_id"] == "sub-1"
    assert seen["body"]["amount"] == "5000"
    assert seen["body"]["invoice_external_ref"] == "project:9"


def test_record_payment_rejects_bad_amount(db_session, monkeypatch):
    from app.services import selfcare

    monkeypatch.setattr(selfcare, "_request_json", lambda *a, **k: {"data": {"id": "x"}})
    assert selfcare.record_payment(db_session, subscriber_id="s", amount="0", external_ref="r") is None
    assert selfcare.record_payment(db_session, subscriber_id="s", amount="nope", external_ref="r") is None


def test_trigger_fires_on_paid_order(db_session, monkeypatch):
    calls = []
    import app.services.events.handlers.selfcare_customer as handler

    monkeypatch.setattr(handler, "push_sales_order_payment_to_selfcare", lambda db, so: calls.append(so))

    paid = SimpleNamespace(payment_status=SalesOrderPaymentStatus.paid)
    sales_orders._sync_sales_order_payment_to_sub(db_session, paid)
    assert calls == [paid]


def test_trigger_skips_unpaid_order(db_session, monkeypatch):
    calls = []
    import app.services.events.handlers.selfcare_customer as handler

    monkeypatch.setattr(handler, "push_sales_order_payment_to_selfcare", lambda db, so: calls.append(so))

    pending = SimpleNamespace(payment_status=SalesOrderPaymentStatus.pending)
    sales_orders._sync_sales_order_payment_to_sub(db_session, pending)
    assert calls == []


def test_push_skips_when_nothing_paid(db_session, monkeypatch):
    from app.services import selfcare
    from app.services.events.handlers import selfcare_customer

    recorded = []
    monkeypatch.setattr(selfcare, "record_payment", lambda *a, **k: recorded.append(k))
    # amount_paid None → no payment pushed (best-effort no-op).
    selfcare_customer.push_sales_order_payment_to_selfcare(db_session, SimpleNamespace(amount_paid=None, id="1"))
    assert recorded == []
