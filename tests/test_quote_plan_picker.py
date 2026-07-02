"""Quote line-item plan picker: a chosen dotmac_sub offer is stored as
metadata_.sub_offer_id and flows to the sales-order → sub subscription sync."""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.models.person import Person
from app.schemas.crm.sales import QuoteCreate
from app.services.crm.sales.service import quote_line_items, quotes
from app.services.crm.web_quotes import (
    QuoteUpsertInput,
    _as_quote_items,
    _parse_quote_line_items,
    update_quote,
)


class _StubRate:
    def __init__(self, percent: str) -> None:
        self.id = uuid.uuid4()
        self.rate = Decimal(percent)


def _person(db) -> Person:
    p = Person(first_name="T", last_name="X", email=f"t-{uuid.uuid4().hex[:8]}@example.com")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def test_parse_maps_sub_offer_id_to_metadata():
    form = QuoteUpsertInput(
        item_description=["Home 100M plan", "Router"],
        item_quantity=["1", "1"],
        item_unit_price=["15000", "20000"],
        item_sub_offer_id=["offer-abc", ""],  # first line is a plan, second is equipment
    )
    parsed = _parse_quote_line_items(_as_quote_items(form))
    assert parsed[0]["metadata_"] == {"sub_offer_id": "offer-abc"}
    # a line with no plan carries metadata_ = None (so an update clears a removed plan)
    assert parsed[1]["metadata_"] is None


def test_parse_line_arrays_stay_aligned_by_index():
    # A plan-only-ish row must not shift the offer id onto the wrong line.
    form = QuoteUpsertInput(
        item_description=["Equipment", "Plan"],
        item_unit_price=["500", "15000"],
        item_sub_offer_id=["", "offer-xyz"],
    )
    parsed = _parse_quote_line_items(_as_quote_items(form))
    assert parsed[0]["metadata_"] is None
    assert parsed[1]["metadata_"] == {"sub_offer_id": "offer-xyz"}


def test_update_quote_persists_and_clears_plan_tag(db_session):
    person = _person(db_session)
    quote = quotes.create(db_session, QuoteCreate(person_id=person.id))
    rate = _StubRate("0")

    # Save a plan-tagged line.
    form = QuoteUpsertInput(
        contact_id=str(person.id),
        status="draft",
        currency="NGN",
        is_active="true",
        item_description=["Home 100M plan"],
        item_quantity=["1"],
        item_unit_price=["15000"],
        item_sub_offer_id=["offer-abc"],
    )
    update_quote(db_session, quote_id=str(quote.id), form=form, tax_rate_get=lambda db, _id: rate)

    lines = quote_line_items.list(
        db_session, quote_id=str(quote.id), order_by="created_at", order_dir="asc", limit=10, offset=0
    )
    assert len(lines) == 1
    assert (lines[0].metadata_ or {}).get("sub_offer_id") == "offer-abc"

    # Re-save the same line without a plan → the tag is cleared.
    form_no_plan = QuoteUpsertInput(
        contact_id=str(person.id),
        status="draft",
        currency="NGN",
        is_active="true",
        item_description=["Home 100M plan"],
        item_quantity=["1"],
        item_unit_price=["15000"],
        item_sub_offer_id=[""],
    )
    update_quote(db_session, quote_id=str(quote.id), form=form_no_plan, tax_rate_get=lambda db, _id: rate)

    lines = quote_line_items.list(
        db_session, quote_id=str(quote.id), order_by="created_at", order_dir="asc", limit=10, offset=0
    )
    assert not (lines[0].metadata_ or {}).get("sub_offer_id")
