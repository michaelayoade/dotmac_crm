"""Tests for quote tax automation: tax_total follows subtotal when a rate is set."""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.models.person import Person
from app.schemas.crm.sales import QuoteCreate, QuoteLineItemCreate, QuoteUpdate
from app.services.crm.sales.service import quote_line_items, quotes
from app.services.crm.web_quotes import QuoteUpsertInput, update_quote


class _StubRate:
    """Minimal tax-rate object accepted by web_quotes tax computations."""

    def __init__(self, percent: str) -> None:
        self.id = uuid.uuid4()
        self.rate = Decimal(percent)


def _person(db) -> Person:
    p = Person(first_name="T", last_name="X", email=f"t-{uuid.uuid4().hex[:8]}@example.com")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _quote(db, person, *, tax_rate=None):
    return quotes.create(db, QuoteCreate(person_id=person.id, tax_rate=tax_rate))


def _add_line(db, quote, *, qty="1", price="0"):
    return quote_line_items.create(
        db,
        QuoteLineItemCreate(quote_id=quote.id, description="Item", quantity=Decimal(qty), unit_price=Decimal(price)),
    )


def test_tax_auto_derives_from_rate(db_session):
    quote = _quote(db_session, _person(db_session), tax_rate=Decimal("7.5"))
    _add_line(db_session, quote, qty="1", price="100000")
    db_session.refresh(quote)
    assert quote.subtotal == Decimal("100000.00")
    assert quote.tax_total == Decimal("7500.00")  # 7.5% of 100000
    assert quote.total == Decimal("107500.00")


def test_tax_follows_subtotal_on_new_line(db_session):
    quote = _quote(db_session, _person(db_session), tax_rate=Decimal("10"))
    _add_line(db_session, quote, qty="1", price="100000")
    _add_line(db_session, quote, qty="1", price="50000")
    db_session.refresh(quote)
    assert quote.subtotal == Decimal("150000.00")
    assert quote.tax_total == Decimal("15000.00")  # 10% of 150000 (recomputed)
    assert quote.total == Decimal("165000.00")


def test_changing_rate_recomputes_tax(db_session):
    quote = _quote(db_session, _person(db_session), tax_rate=Decimal("5"))
    _add_line(db_session, quote, qty="1", price="200000")
    db_session.refresh(quote)
    assert quote.tax_total == Decimal("10000.00")

    quotes.update(db_session, str(quote.id), QuoteUpdate(tax_rate=Decimal("7.5")))
    db_session.refresh(quote)
    assert quote.tax_rate == Decimal("7.50")
    assert quote.tax_total == Decimal("15000.00")  # 7.5% of 200000
    assert quote.total == Decimal("215000.00")


def test_no_rate_keeps_manual_tax(db_session):
    quote = _quote(db_session, _person(db_session))  # tax_rate=None
    quotes.update(db_session, str(quote.id), QuoteUpdate(tax_total=Decimal("999")))
    _add_line(db_session, quote, qty="1", price="100000")
    db_session.refresh(quote)
    # Manual tax_total is preserved (not overwritten) when no rate is set.
    assert quote.tax_rate is None
    assert quote.tax_total == Decimal("999.00")
    assert quote.total == Decimal("100999.00")


def test_removing_line_item_recomputes_subtotal_and_tax(db_session):
    """update_quote must re-derive subtotal AND tax after a line is removed."""
    person = _person(db_session)
    quote = _quote(db_session, person, tax_rate=Decimal("10"))
    _add_line(db_session, quote, qty="1", price="100000")
    _add_line(db_session, quote, qty="1", price="50000")
    db_session.refresh(quote)
    assert quote.subtotal == Decimal("150000.00")
    assert quote.tax_total == Decimal("15000.00")

    rate = _StubRate("10")
    form = QuoteUpsertInput(
        contact_id=str(person.id),
        tax_rate_id=str(rate.id),
        status="draft",
        currency="NGN",
        is_active="true",
        item_description=["Item A"],
        item_quantity=["1"],
        item_unit_price=["100000"],
    )
    update_quote(db_session, quote_id=str(quote.id), form=form, tax_rate_get=lambda db, _id: rate)

    db_session.refresh(quote)
    # Only the remaining line counts: subtotal drops to 100000 and tax follows it.
    assert quote.subtotal == Decimal("100000.00")
    assert quote.tax_total == Decimal("10000.00")  # 10% of the reduced subtotal
    assert quote.total == Decimal("110000.00")
