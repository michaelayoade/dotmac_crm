from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.sales_order import SalesOrder, SalesOrderLine
from app.schemas.field import FieldSalesOrderCreate
from app.schemas.sales_order import SalesOrderCreate
from app.services import sales_orders as sales_order_service
from app.services.common import coerce_uuid


class FieldSalesOrders:
    @staticmethod
    def list_mine(
        db: Session,
        person_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SalesOrder]:
        person_uuid = coerce_uuid(person_id)
        query = (
            db.query(SalesOrder)
            .filter(SalesOrder.is_active.is_(True))
            .filter(SalesOrder.metadata_["created_from"].as_string() == "field_mobile")
            .filter(SalesOrder.metadata_["created_by_person_id"].as_string() == str(person_uuid))
            .order_by(SalesOrder.created_at.desc())
        )
        orders = query.offset(offset).limit(limit).all()

        bind = db.get_bind()
        if bind is not None and bind.dialect.name == "sqlite":
            rows = (
                db.query(SalesOrder).filter(SalesOrder.is_active.is_(True)).order_by(SalesOrder.created_at.desc()).all()
            )
            orders = [
                row
                for row in rows
                if isinstance(row.metadata_, dict)
                and row.metadata_.get("created_from") == "field_mobile"
                and row.metadata_.get("created_by_person_id") == str(person_uuid)
            ][offset : offset + limit]

        for order in orders:
            _ = order.lines
        return orders

    @staticmethod
    def create(
        db: Session,
        creator_person_id: str,
        payload: FieldSalesOrderCreate,
    ) -> SalesOrder:
        order = sales_order_service.sales_orders.create(
            db,
            SalesOrderCreate(
                person_id=payload.person_id,
                currency=payload.currency.upper(),
                notes=payload.notes,
                metadata_={
                    "created_from": "field_mobile",
                    "created_by_person_id": str(coerce_uuid(creator_person_id)),
                },
            ),
        )

        for item in payload.lines:
            amount = Decimal(item.quantity) * Decimal(item.unit_price)
            db.add(
                SalesOrderLine(
                    sales_order_id=order.id,
                    inventory_item_id=item.inventory_item_id,
                    description=item.description,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    amount=amount,
                )
            )

        db.flush()
        sales_order_service._recalculate_order_totals(db, str(order.id))
        db.commit()
        return sales_order_service.sales_orders.get(db, str(order.id))


field_sales_orders = FieldSalesOrders()
