"""Tests for technician-created field sales orders."""

from decimal import Decimal

from app.models.inventory import InventoryItem
from app.schemas.field import FieldSalesOrderCreate, FieldSalesOrderLineCreate
from app.services.field.sales_orders import field_sales_orders


def test_field_create_sales_order_with_lines(db_session, person):
    item = InventoryItem(name="Router", sku="RTR-1")
    db_session.add(item)
    db_session.commit()

    order = field_sales_orders.create(
        db_session,
        str(person.id),
        FieldSalesOrderCreate(
            person_id=person.id,
            notes="Customer wants installation",
            lines=[
                FieldSalesOrderLineCreate(
                    inventory_item_id=item.id,
                    description="Router",
                    quantity=Decimal("2"),
                    unit_price=Decimal("15000"),
                )
            ],
        ),
    )

    assert order.person_id == person.id
    assert order.metadata_["created_from"] == "field_mobile"
    assert order.metadata_["created_by_person_id"] == str(person.id)
    assert order.total == Decimal("30000.00")
    assert order.lines[0].description == "Router"


def test_field_sales_orders_are_scoped_to_creator(db_session, person):
    other_person = person.__class__(
        first_name="Other",
        last_name="Tech",
        email="other-field-sales@example.com",
    )
    db_session.add(other_person)
    db_session.commit()

    field_sales_orders.create(
        db_session,
        str(person.id),
        FieldSalesOrderCreate(
            person_id=person.id,
            lines=[
                FieldSalesOrderLineCreate(
                    description="Install service",
                    quantity=Decimal("1"),
                    unit_price=Decimal("25000"),
                )
            ],
        ),
    )

    assert len(field_sales_orders.list_mine(db_session, str(person.id))) == 1
    assert field_sales_orders.list_mine(db_session, str(other_person.id)) == []
