from decimal import Decimal
from unittest.mock import patch

from app.models.crm.sales import Quote
from app.models.projects import Project, ProjectType
from app.schemas.sales_order import SalesOrderCreate, SalesOrderLineCreate
from app.services import sales_orders as sales_orders_service


def _projects_for_sales_order(db_session, sales_order_id):
    rows = db_session.query(Project).filter(Project.is_active.is_(True)).all()
    return [
        row
        for row in rows
        if isinstance(row.metadata_, dict) and str(row.metadata_.get("sales_order_id")) == str(sales_order_id)
    ]


def test_manual_sales_order_creates_project_with_selected_type(db_session, person):
    payload = SalesOrderCreate(
        person_id=person.id,
        total=Decimal("107.50"),
        amount_paid=Decimal("0.00"),
        metadata_={"project_type": ProjectType.fiber_optics_installation.value},
    )
    order = sales_orders_service.sales_orders.create(db_session, payload)

    linked_projects = _projects_for_sales_order(db_session, order.id)
    assert len(linked_projects) == 1
    project = linked_projects[0]
    assert project.project_type == ProjectType.fiber_optics_installation
    assert project.owner_person_id == person.id


def test_quote_linked_sales_order_does_not_create_fallback_project(db_session, person):
    quote = Quote(person_id=person.id)
    db_session.add(quote)
    db_session.commit()
    db_session.refresh(quote)

    payload = SalesOrderCreate(
        person_id=person.id,
        quote_id=quote.id,
        total=Decimal("50.00"),
        amount_paid=Decimal("0.00"),
    )
    order = sales_orders_service.sales_orders.create(db_session, payload)

    linked_projects = _projects_for_sales_order(db_session, order.id)
    assert linked_projects == []


def test_manual_sales_order_project_creation_is_idempotent(db_session, person):
    payload = SalesOrderCreate(
        person_id=person.id,
        total=Decimal("10.00"),
        amount_paid=Decimal("0.00"),
    )
    order = sales_orders_service.sales_orders.create(db_session, payload)
    db_session.refresh(order)

    sales_orders_service._ensure_project_for_manual_sales_order(db_session, order)
    sales_orders_service._ensure_project_for_manual_sales_order(db_session, order)

    linked_projects = _projects_for_sales_order(db_session, order.id)
    assert len(linked_projects) == 1


def test_create_from_quote_does_not_create_fallback_project(db_session, person):
    quote = Quote(person_id=person.id)
    db_session.add(quote)
    db_session.commit()
    db_session.refresh(quote)

    order = sales_orders_service.sales_orders.create_from_quote(db_session, str(quote.id))
    linked_projects = _projects_for_sales_order(db_session, order.id)
    assert linked_projects == []


@patch("app.services.events.handlers.splynx_customer.ensure_installation_invoice_for_sales_order")
def test_sales_order_line_create_retries_splynx_installation_invoice(mock_retry, db_session, person):
    order = sales_orders_service.sales_orders.create(
        db_session,
        SalesOrderCreate(
            person_id=person.id,
            total=Decimal("0.00"),
            amount_paid=Decimal("0.00"),
            metadata_={"project_type": ProjectType.fiber_optics_installation.value},
        ),
    )

    sales_orders_service.sales_order_lines.create(
        db_session,
        SalesOrderLineCreate(
            sales_order_id=order.id,
            description="Fiber Optics Installation",
            quantity=Decimal("1.00"),
            unit_price=Decimal("1000.00"),
            amount=Decimal("1000.00"),
        ),
    )

    mock_retry.assert_called_once_with(db_session, order.id)
