from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.models.sales_order import (
    SalesOrder,
    SalesOrderLine,
    SalesOrderPaymentStatus,
    SalesOrderStatus,
)
from app.models.crm.sales import Quote
from app.models.person import Person
from app.models.sequence import DocumentSequence
from app.services.common import (
    apply_ordering,
    apply_pagination,
    coerce_uuid,
    ensure_exists,
    get_by_id,
    round_money,
    validate_enum,
)
from app.services.response import ListResponseMixin

def _ensure_person(db: Session, person_id: str):
    person = get_by_id(db, Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    return person


def _next_sequence_value(db: Session, key: str, start_value: int = 1) -> int:
    sequence = (
        db.query(DocumentSequence)
        .filter(DocumentSequence.key == key)
        .with_for_update()
        .first()
    )
    if not sequence:
        sequence = DocumentSequence(key=key, next_value=start_value)
        db.add(sequence)
        db.flush()
    value = sequence.next_value
    sequence.next_value = value + 1
    db.flush()
    return value


def _generate_order_number(db: Session) -> str:
    value = _next_sequence_value(db, "sales_order_number", 1)
    return f"SO-{value:06d}"


def _apply_payment_fields(sales_order: SalesOrder, data: dict) -> None:
    if "amount_paid" in data or "total" in data:
        total = Decimal(data.get("total") or sales_order.total or 0)
        amount_paid = Decimal(data.get("amount_paid") or sales_order.amount_paid or 0)
        balance_due = round_money(total - amount_paid)
        sales_order.total = round_money(total)
        sales_order.amount_paid = round_money(amount_paid)
        sales_order.balance_due = balance_due
        if total > 0 and balance_due <= 0:
            sales_order.payment_status = SalesOrderPaymentStatus.paid
            if not sales_order.paid_at:
                sales_order.paid_at = datetime.now(timezone.utc)
        elif amount_paid > 0:
            sales_order.payment_status = SalesOrderPaymentStatus.partial
        else:
            sales_order.payment_status = SalesOrderPaymentStatus.pending
    if sales_order.payment_status == SalesOrderPaymentStatus.paid:
        if sales_order.status in {SalesOrderStatus.draft, SalesOrderStatus.confirmed}:
            sales_order.status = SalesOrderStatus.paid
    elif sales_order.payment_status == SalesOrderPaymentStatus.waived:
        if sales_order.status == SalesOrderStatus.draft:
            sales_order.status = SalesOrderStatus.confirmed


def _recalculate_order_totals(db: Session, sales_order_id: str) -> None:
    sales_order = db.get(SalesOrder, coerce_uuid(sales_order_id))
    if not sales_order:
        return
    totals = (
        db.query(func.coalesce(func.sum(SalesOrderLine.amount), 0))
        .filter(SalesOrderLine.sales_order_id == sales_order.id)
        .filter(SalesOrderLine.is_active.is_(True))
        .scalar()
    )
    subtotal = round_money(Decimal(totals or 0))
    sales_order.subtotal = subtotal
    sales_order.total = round_money(subtotal + Decimal(sales_order.tax_total or 0))
    _apply_payment_fields(sales_order, {"total": sales_order.total})
    db.flush()


def _ensure_fulfillment(db: Session, sales_order: SalesOrder) -> None:
    """Placeholder for fulfillment actions once implemented."""
    return None


class SalesOrders(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload):
        data = payload.model_dump()
        if data.get("status"):
            data["status"] = validate_enum(data["status"], SalesOrderStatus, "status")
        if data.get("payment_status"):
            data["payment_status"] = validate_enum(
                data["payment_status"], SalesOrderPaymentStatus, "payment_status"
            )
        total_value = Decimal(data.get("total") or 0)
        amount_paid_value = Decimal(data.get("amount_paid") or 0)

        _ensure_person(db, data.get("person_id"))
        if data.get("quote_id"):
            quote = get_by_id(db, Quote, data["quote_id"])
            if not quote:
                raise HTTPException(status_code=404, detail="Quote not found")
            existing = (
                db.query(SalesOrder)
                .filter(SalesOrder.quote_id == quote.id)
                .first()
            )
            if existing:
                raise HTTPException(
                    status_code=400, detail="Sales order already exists for this quote"
                )

        if not data.get("order_number"):
            data["order_number"] = _generate_order_number(db)

        if data.get("total") is not None and data.get("balance_due") is None:
            data["amount_paid"] = round_money(amount_paid_value)
            data["balance_due"] = round_money(total_value - amount_paid_value)

        sales_order = SalesOrder(**data)
        _apply_payment_fields(sales_order, data)
        db.add(sales_order)
        db.commit()
        db.refresh(sales_order)
        _ensure_fulfillment(db, sales_order)
        db.commit()
        db.refresh(sales_order)
        return sales_order

    @staticmethod
    def create_from_quote(db: Session, quote_id: str) -> SalesOrder:
        quote = db.get(
            Quote,
            coerce_uuid(quote_id),
            options=[selectinload(Quote.line_items)],
        )
        if not quote:
            raise HTTPException(status_code=404, detail="Quote not found")
        existing = (
            db.query(SalesOrder).filter(SalesOrder.quote_id == quote.id).first()
        )
        if existing:
            return existing

        order_number = _generate_order_number(db)
        sales_order = SalesOrder(
            quote_id=quote.id,
            person_id=quote.person_id,
            order_number=order_number,
            status=SalesOrderStatus.confirmed,
            payment_status=SalesOrderPaymentStatus.pending,
            currency=quote.currency,
            subtotal=quote.subtotal,
            tax_total=quote.tax_total,
            total=quote.total,
            amount_paid=Decimal("0.00"),
            balance_due=quote.total,
        )
        db.add(sales_order)
        db.flush()

        for item in quote.line_items:
            amount = item.amount
            if amount is None:
                amount = Decimal(item.quantity or 0) * Decimal(item.unit_price or 0)
            line = SalesOrderLine(
                sales_order_id=sales_order.id,
                inventory_item_id=item.inventory_item_id,
                description=item.description,
                quantity=item.quantity,
                unit_price=item.unit_price,
                amount=amount,
                metadata_=item.metadata_,
            )
            db.add(line)

        db.commit()
        db.refresh(sales_order)
        return sales_order

    @staticmethod
    def get(db: Session, sales_order_id: str):
        sales_order = db.get(
            SalesOrder,
            coerce_uuid(sales_order_id),
            options=[selectinload(SalesOrder.lines)],
        )
        if not sales_order:
            raise HTTPException(status_code=404, detail="Sales order not found")
        return sales_order

    @staticmethod
    def list(
        db: Session,
        person_id: str | None,
        quote_id: str | None,
        status: str | None,
        payment_status: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(SalesOrder)
        if person_id:
            query = query.filter(SalesOrder.person_id == coerce_uuid(person_id))
        if quote_id:
            query = query.filter(SalesOrder.quote_id == coerce_uuid(quote_id))
        if status:
            status_value = validate_enum(status, SalesOrderStatus, "status")
            query = query.filter(SalesOrder.status == status_value)
        if payment_status:
            payment_value = validate_enum(
                payment_status, SalesOrderPaymentStatus, "payment_status"
            )
            query = query.filter(SalesOrder.payment_status == payment_value)
        if is_active is None:
            query = query.filter(SalesOrder.is_active.is_(True))
        else:
            query = query.filter(SalesOrder.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": SalesOrder.created_at, "updated_at": SalesOrder.updated_at},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, sales_order_id: str, payload):
        sales_order = db.get(SalesOrder, coerce_uuid(sales_order_id))
        if not sales_order:
            raise HTTPException(status_code=404, detail="Sales order not found")
        data = payload.model_dump(exclude_unset=True)
        if "status" in data:
            data["status"] = validate_enum(data["status"], SalesOrderStatus, "status")
        if "payment_status" in data:
            data["payment_status"] = validate_enum(
                data["payment_status"], SalesOrderPaymentStatus, "payment_status"
            )
        if data.get("person_id"):
            _ensure_person(db, data["person_id"])
        if data.get("quote_id"):
            quote = get_by_id(db, Quote, data["quote_id"])
            if not quote:
                raise HTTPException(status_code=404, detail="Quote not found")
            existing = (
                db.query(SalesOrder)
                .filter(SalesOrder.quote_id == quote.id, SalesOrder.id != sales_order.id)
                .first()
            )
            if existing:
                raise HTTPException(
                    status_code=400, detail="Sales order already exists for this quote"
                )

        if data.get("payment_status") == SalesOrderPaymentStatus.paid:
            resolved_total = Decimal(data.get("total") or sales_order.total or 0)
            resolved_amount_paid = Decimal(
                data.get("amount_paid") or sales_order.amount_paid or 0
            )
            if resolved_amount_paid < resolved_total:
                data["amount_paid"] = round_money(resolved_total)
            data["balance_due"] = Decimal("0.00")
            if "paid_at" not in data or data.get("paid_at") is None:
                data["paid_at"] = datetime.now(timezone.utc)
            if "status" not in data and sales_order.status in {
                SalesOrderStatus.draft,
                SalesOrderStatus.confirmed,
            }:
                data["status"] = SalesOrderStatus.paid

        for key, value in data.items():
            setattr(sales_order, key, value)

        _apply_payment_fields(sales_order, data)
        _ensure_fulfillment(db, sales_order)
        db.commit()
        db.refresh(sales_order)
        return sales_order

    @staticmethod
    def delete(db: Session, sales_order_id: str):
        sales_order = db.get(SalesOrder, coerce_uuid(sales_order_id))
        if not sales_order:
            raise HTTPException(status_code=404, detail="Sales order not found")
        sales_order.is_active = False
        db.commit()


class SalesOrderLines(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload):
        sales_order = db.get(SalesOrder, payload.sales_order_id)
        if not sales_order:
            raise HTTPException(status_code=404, detail="Sales order not found")
        data = payload.model_dump()
        if not data.get("amount"):
            data["amount"] = Decimal(data.get("quantity") or 0) * Decimal(
                data.get("unit_price") or 0
            )
        line = SalesOrderLine(**data)
        db.add(line)
        db.flush()
        _recalculate_order_totals(db, str(sales_order.id))
        db.commit()
        db.refresh(line)
        return line

    @staticmethod
    def update(db: Session, line_id: str, payload):
        line = db.get(SalesOrderLine, coerce_uuid(line_id))
        if not line:
            raise HTTPException(status_code=404, detail="Sales order line not found")
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(line, key, value)
        if "quantity" in data or "unit_price" in data:
            line.amount = Decimal(line.quantity or 0) * Decimal(line.unit_price or 0)
        db.flush()
        _recalculate_order_totals(db, str(line.sales_order_id))
        db.commit()
        db.refresh(line)
        return line

    @staticmethod
    def list(
        db: Session,
        sales_order_id: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(SalesOrderLine)
        if sales_order_id:
            query = query.filter(
                SalesOrderLine.sales_order_id == coerce_uuid(sales_order_id)
            )
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": SalesOrderLine.created_at},
        )
        return apply_pagination(query, limit, offset).all()


sales_orders = SalesOrders()
sales_order_lines = SalesOrderLines()
