from decimal import Decimal

from app.schemas.crm.sales import QuoteCreate, QuoteLineItemCreate, QuoteUpdate
from app.schemas.sales_order import SalesOrderCreate, SalesOrderLineCreate, SalesOrderLineUpdate
from app.services import sales_orders as sales_order_service
from app.services.crm import sales as sales_service
from app.models.projects import Project


def test_sales_order_created_on_quote_acceptance(db_session, person):
    quote = sales_service.Quotes.create(
        db_session,
        QuoteCreate(person_id=person.id),
    )
    sales_service.CrmQuoteLineItems.create(
        db_session,
        QuoteLineItemCreate(
            quote_id=quote.id,
            description="Installation",
            quantity=Decimal("2.000"),
            unit_price=Decimal("150.00"),
        ),
    )

    sales_service.Quotes.update(
        db_session,
        str(quote.id),
        QuoteUpdate(status="accepted"),
    )

    orders = sales_order_service.sales_orders.list(
        db_session,
        person_id=str(person.id),
        account_id=None,
        quote_id=str(quote.id),
        status=None,
        payment_status=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )

    assert len(orders) == 1
    order = orders[0]
    assert order.order_number is not None
    assert order.total == quote.total

    lines = sales_order_service.sales_order_lines.list(
        db_session,
        sales_order_id=str(order.id),
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(lines) == 1
    assert lines[0].description == "Installation"


def test_sales_order_lines_recalculate_totals(db_session, person):
    order = sales_order_service.sales_orders.create(
        db_session,
        SalesOrderCreate(person_id=person.id),
    )

    sales_order_service.sales_order_lines.create(
        db_session,
        SalesOrderLineCreate(
            sales_order_id=order.id,
            description="Equipment",
            quantity=Decimal("3.000"),
            unit_price=Decimal("100.00"),
        ),
    )

    updated = sales_order_service.sales_orders.get(db_session, str(order.id))
    assert updated.subtotal == Decimal("300.00")
    assert updated.total == Decimal("300.00")
    assert updated.balance_due == Decimal("300.00")

    line = sales_order_service.sales_order_lines.list(
        db_session,
        sales_order_id=str(order.id),
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )[0]
    sales_order_service.sales_order_lines.update(
        db_session,
        str(line.id),
        SalesOrderLineUpdate(unit_price=Decimal("200.00")),
    )

    updated = sales_order_service.sales_orders.get(db_session, str(order.id))
    assert updated.subtotal == Decimal("600.00")
    assert updated.total == Decimal("600.00")


def test_sales_order_paid_creates_service_order_and_project(db_session, person, subscriber_account):
    order = sales_order_service.sales_orders.create(
        db_session,
        SalesOrderCreate(
            person_id=person.id,
            account_id=subscriber_account.id,
            total=Decimal("500.00"),
            amount_paid=Decimal("500.00"),
        ),
    )

    assert order.service_order_id is not None
    project = (
        db_session.query(Project)
        .filter(Project.service_order_id == order.service_order_id)
        .first()
    )
    assert project is not None
