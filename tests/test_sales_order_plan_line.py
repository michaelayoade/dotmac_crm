"""A sales-order line can carry a sub offer (plan) tag, which the subscription
sync reads — the destination of the sales-order-form plan picker."""

from decimal import Decimal

from app.models.sales_order import SalesOrderLine
from app.schemas.sales_order import SalesOrderCreate
from app.services import sales_orders as sales_orders_service
from app.services.events.handlers import selfcare_customer


def _line(db_session, person, *, metadata=None) -> SalesOrderLine:
    order = sales_orders_service.sales_orders.create(
        db_session,
        SalesOrderCreate(person_id=person.id, total=Decimal("15000.00"), amount_paid=Decimal("0.00")),
    )
    line = SalesOrderLine(
        sales_order_id=order.id,
        description="Home 100M plan",
        quantity=Decimal("1"),
        unit_price=Decimal("15000"),
        amount=Decimal("15000"),
        metadata_=metadata,
    )
    db_session.add(line)
    db_session.commit()
    db_session.refresh(line)
    return line


def test_line_persists_and_exposes_plan_tag(db_session, person):
    line = _line(db_session, person, metadata={"sub_offer_id": "offer-abc"})
    # Stored on the model (JSON column round-trips)...
    assert (line.metadata_ or {}).get("sub_offer_id") == "offer-abc"
    # ...and the subscription sync recognises it as a plan line.
    assert selfcare_customer._line_offer_ref(line) == "offer-abc"


def test_untagged_line_is_not_a_plan(db_session, person):
    line = _line(db_session, person, metadata=None)
    assert selfcare_customer._line_offer_ref(line) is None
