"""Tests for quote line-item discounts + total recalculation."""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.models.person import Person
from app.schemas.crm.sales import QuoteCreate, QuoteLineItemCreate, QuoteLineItemUpdate
from app.services.crm.sales.service import quote_line_items, quotes


def _person(db) -> Person:
    p = Person(first_name="Q", last_name="D", email=f"q-{uuid.uuid4().hex[:8]}@example.com")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _quote(db, person):
    return quotes.create(db, QuoteCreate(person_id=person.id))


def _add_line(db, quote, *, qty="1", price="0", discount="0"):
    return quote_line_items.create(
        db,
        QuoteLineItemCreate(
            quote_id=quote.id,
            description="Router",
            quantity=Decimal(qty),
            unit_price=Decimal(price),
            discount_percent=Decimal(discount),
        ),
    )


def test_line_discount_reduces_amount_and_subtotal(db_session):
    quote = _quote(db_session, _person(db_session))
    item = _add_line(db_session, quote, qty="2", price="10000", discount="10")
    assert item.amount == Decimal("18000.00")  # 2 * 10000 * 0.90
    db_session.refresh(quote)
    assert quote.subtotal == Decimal("18000.00")
    assert quote.total == Decimal("18000.00")


def test_no_discount_is_full_price(db_session):
    quote = _quote(db_session, _person(db_session))
    item = _add_line(db_session, quote, qty="3", price="5000")
    assert item.discount_percent == Decimal("0.00")
    assert item.amount == Decimal("15000.00")


def test_full_discount_yields_zero(db_session):
    quote = _quote(db_session, _person(db_session))
    item = _add_line(db_session, quote, qty="1", price="20000", discount="100")
    assert item.amount == Decimal("0.00")


def test_update_discount_recomputes_amount(db_session):
    quote = _quote(db_session, _person(db_session))
    item = _add_line(db_session, quote, qty="1", price="10000")
    assert item.amount == Decimal("10000.00")

    updated = quote_line_items.update(
        db_session, str(item.id), QuoteLineItemUpdate(discount_percent=Decimal("25"))
    )
    assert updated.amount == Decimal("7500.00")
    db_session.refresh(quote)
    assert quote.subtotal == Decimal("7500.00")


def test_amount_is_server_derived(db_session):
    """A client-supplied amount is ignored; the server recomputes from qty/price/discount."""
    quote = _quote(db_session, _person(db_session))
    item = quote_line_items.create(
        db_session,
        QuoteLineItemCreate(
            quote_id=quote.id,
            description="Tamper",
            quantity=Decimal("1"),
            unit_price=Decimal("10000"),
            discount_percent=Decimal("10"),
            amount=Decimal("999999"),
        ),
    )
    assert item.amount == Decimal("9000.00")


def test_mixed_lines_subtotal(db_session):
    quote = _quote(db_session, _person(db_session))
    _add_line(db_session, quote, qty="1", price="10000", discount="0")
    _add_line(db_session, quote, qty="1", price="10000", discount="50")
    db_session.refresh(quote)
    assert quote.subtotal == Decimal("15000.00")  # 10000 + 5000
