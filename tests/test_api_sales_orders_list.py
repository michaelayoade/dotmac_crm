"""Regression tests for GET /sales-orders filter plumbing.

The API endpoint used to pass a legacy ``account_id`` value positionally into
the service ``list()`` signature (which has no such parameter), shifting every
subsequent filter one slot to the right (account_id landed in quote_id, quote_id
in status, ...) and finally raising ``TypeError: got multiple values for
'limit'`` — every call to the list endpoint 500'd. ``account_id`` has no backing
column (SubscriberAccount was removed), so the parameter was dropped and the
remaining filters realigned.
"""

from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import get_db
from app.models.crm.sales import Quote, QuoteStatus
from app.models.sales_order import SalesOrder, SalesOrderStatus


def _client(db_session):
    from app.api.sales_orders import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _seed(db_session, person):
    quote_a = Quote(person_id=person.id, status=QuoteStatus.accepted, currency="NGN")
    quote_b = Quote(person_id=person.id, status=QuoteStatus.accepted, currency="NGN")
    db_session.add_all([quote_a, quote_b])
    db_session.commit()
    so_a = SalesOrder(
        person_id=person.id,
        quote_id=quote_a.id,
        status=SalesOrderStatus.draft,
        total=Decimal("10000.00"),
    )
    so_b = SalesOrder(
        person_id=person.id,
        quote_id=quote_b.id,
        status=SalesOrderStatus.confirmed,
        total=Decimal("20000.00"),
    )
    db_session.add_all([so_a, so_b])
    db_session.commit()
    return quote_a, quote_b, so_a, so_b


def test_list_sales_orders_returns_200(db_session, person):
    _seed(db_session, person)
    resp = _client(db_session).get("/sales-orders")
    assert resp.status_code == 200, resp.text
    assert resp.json()["count"] == 2


def test_list_sales_orders_quote_id_filters_by_quote(db_session, person):
    quote_a, _, so_a, _ = _seed(db_session, person)
    resp = _client(db_session).get("/sales-orders", params={"quote_id": str(quote_a.id)})
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert [item["id"] for item in items] == [str(so_a.id)]


def test_list_sales_orders_status_filter_lands_in_status_slot(db_session, person):
    _, _, _, so_b = _seed(db_session, person)
    resp = _client(db_session).get("/sales-orders", params={"status": "confirmed"})
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert [item["id"] for item in items] == [str(so_b.id)]


def test_list_sales_orders_person_id_filters_by_person(db_session, person):
    _seed(db_session, person)
    other = person.__class__(first_name="Other", last_name="Person", email="other-so-list@example.com")
    db_session.add(other)
    db_session.commit()
    so_other = SalesOrder(person_id=other.id, status=SalesOrderStatus.draft, total=Decimal("5000.00"))
    db_session.add(so_other)
    db_session.commit()

    resp = _client(db_session).get("/sales-orders", params={"person_id": str(other.id)})
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert [item["id"] for item in items] == [str(so_other.id)]
