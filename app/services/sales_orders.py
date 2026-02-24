from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.models.crm.sales import Quote
from app.models.person import Person
from app.models.projects import Project, ProjectStatus, ProjectTemplate, ProjectType
from app.models.sales_order import (
    SalesOrder,
    SalesOrderLine,
    SalesOrderPaymentStatus,
    SalesOrderStatus,
)
from app.models.sequence import DocumentSequence
from app.schemas.projects import ProjectCreate
from app.services import projects as projects_service
from app.services.common import (
    apply_ordering,
    apply_pagination,
    coerce_uuid,
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


def _parse_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        raise HTTPException(status_code=400, detail="Invalid decimal value")


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid datetime value") from exc


def _next_sequence_value(db: Session, key: str, start_value: int = 1) -> int:
    sequence = db.query(DocumentSequence).filter(DocumentSequence.key == key).with_for_update().first()
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
                sales_order.paid_at = datetime.now(UTC)
        elif amount_paid > 0:
            sales_order.payment_status = SalesOrderPaymentStatus.partial
        else:
            sales_order.payment_status = SalesOrderPaymentStatus.pending
    if sales_order.payment_status == SalesOrderPaymentStatus.paid:
        if sales_order.status in {SalesOrderStatus.draft, SalesOrderStatus.confirmed}:
            sales_order.status = SalesOrderStatus.paid
    elif sales_order.payment_status == SalesOrderPaymentStatus.waived and sales_order.status == SalesOrderStatus.draft:
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


def _resolve_project_type(value: str | None) -> ProjectType | None:
    if not value:
        return None
    legacy_map = {
        "radio_installation": ProjectType.air_fiber_installation,
        "radio_fiber_relocation": ProjectType.air_fiber_relocation,
    }
    if value in legacy_map:
        return legacy_map[value]
    try:
        return ProjectType(value)
    except ValueError:
        return None


def _find_template_for_project_type(db: Session, project_type: ProjectType) -> ProjectTemplate | None:
    return (
        db.query(ProjectTemplate)
        .filter(ProjectTemplate.is_active.is_(True))
        .filter(ProjectTemplate.project_type == project_type)
        .order_by(ProjectTemplate.created_at.desc())
        .first()
    )


def _find_existing_project_for_sales_order(db: Session, sales_order_id: object) -> Project | None:
    existing = (
        db.query(Project)
        .filter(Project.is_active.is_(True))
        .filter(Project.metadata_["sales_order_id"].as_string() == str(sales_order_id))
        .first()
    )
    if existing:
        return existing

    # SQLite JSON path comparisons are not reliable across SQLAlchemy/SQLite builds.
    # Fall back to an in-Python metadata check to keep this idempotent in tests/dev.
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "sqlite":
        rows = db.query(Project).filter(Project.is_active.is_(True)).all()
        for row in rows:
            metadata = row.metadata_ if isinstance(row.metadata_, dict) else {}
            if str(metadata.get("sales_order_id")) == str(sales_order_id):
                return row
    return None


def _build_project_name_for_sales_order(
    *,
    base_name: str,
    owner_label: str | None,
    sales_order_id: object,
    max_length: int = 160,
) -> str:
    if owner_label:
        candidate = f"{base_name} - {owner_label}"
    else:
        candidate = f"{base_name} - SO {str(sales_order_id)[:8].upper()}"
    cleaned = " ".join(candidate.split()).strip()
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[:max_length].rstrip()


def _ensure_project_for_manual_sales_order(db: Session, sales_order: SalesOrder) -> Project | None:
    # Quote-driven flow already creates projects on quote acceptance.
    if sales_order.quote_id:
        return None

    existing = _find_existing_project_for_sales_order(db, sales_order.id)
    if existing:
        return existing

    metadata = sales_order.metadata_ if isinstance(sales_order.metadata_, dict) else {}
    project_type_value = metadata.get("project_type") if isinstance(metadata, dict) else None
    project_type = _resolve_project_type(project_type_value if isinstance(project_type_value, str) else None)
    template = _find_template_for_project_type(db, project_type) if project_type else None

    person = db.get(Person, sales_order.person_id)
    owner_label = None
    if person:
        owner_label = person.display_name or person.email
    base_name = project_type.value.replace("_", " ").title() if project_type else "Project"
    project_name = _build_project_name_for_sales_order(
        base_name=base_name,
        owner_label=owner_label,
        sales_order_id=sales_order.id,
    )

    project_metadata = {}
    project_metadata["sales_order_id"] = str(sales_order.id)
    if project_type:
        project_metadata["project_type"] = project_type.value

    payload = ProjectCreate(
        name=project_name,
        project_type=project_type,
        project_template_id=template.id if template else None,
        status=ProjectStatus.active,
        owner_person_id=sales_order.person_id,
        metadata_=project_metadata or None,
    )
    return projects_service.projects.create(db, payload)


class SalesOrders(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload):
        data = payload.model_dump()
        # Legacy schema still includes fields removed from the model.
        data.pop("account_id", None)
        data.pop("invoice_id", None)
        if data.get("status"):
            data["status"] = validate_enum(data["status"], SalesOrderStatus, "status")
        if data.get("payment_status"):
            data["payment_status"] = validate_enum(data["payment_status"], SalesOrderPaymentStatus, "payment_status")
        total_value = Decimal(data.get("total") or 0)
        amount_paid_value = Decimal(data.get("amount_paid") or 0)

        _ensure_person(db, data.get("person_id"))
        if data.get("quote_id"):
            quote = get_by_id(db, Quote, data["quote_id"])
            if not quote:
                raise HTTPException(status_code=404, detail="Quote not found")
            existing = db.query(SalesOrder).filter(SalesOrder.quote_id == quote.id).first()
            if existing:
                raise HTTPException(status_code=400, detail="Sales order already exists for this quote")

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
        _ensure_project_for_manual_sales_order(db, sales_order)
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
        existing = db.query(SalesOrder).filter(SalesOrder.quote_id == quote.id).first()
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
            payment_value = validate_enum(payment_status, SalesOrderPaymentStatus, "payment_status")
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
            data["payment_status"] = validate_enum(data["payment_status"], SalesOrderPaymentStatus, "payment_status")
        if data.get("person_id"):
            _ensure_person(db, data["person_id"])
        if data.get("quote_id"):
            quote = get_by_id(db, Quote, data["quote_id"])
            if not quote:
                raise HTTPException(status_code=404, detail="Quote not found")
            existing = (
                db.query(SalesOrder).filter(SalesOrder.quote_id == quote.id, SalesOrder.id != sales_order.id).first()
            )
            if existing:
                raise HTTPException(status_code=400, detail="Sales order already exists for this quote")

        if data.get("payment_status") == SalesOrderPaymentStatus.paid:
            resolved_total = Decimal(data.get("total") or sales_order.total or 0)
            resolved_amount_paid = Decimal(data.get("amount_paid") or sales_order.amount_paid or 0)
            if resolved_amount_paid < resolved_total:
                data["amount_paid"] = round_money(resolved_total)
            data["balance_due"] = Decimal("0.00")
            if "paid_at" not in data or data.get("paid_at") is None:
                data["paid_at"] = datetime.now(UTC)
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
    def update_from_input(
        db: Session,
        sales_order_id: str,
        *,
        status: str | None = None,
        payment_status: str | None = None,
        total: str | None = None,
        amount_paid: str | None = None,
        paid_at: str | None = None,
        notes: str | None = None,
    ):
        """Update sales order using raw string inputs (e.g., from web forms)."""
        update_data: dict[str, Any] = {}
        if status:
            update_data["status"] = validate_enum(status, SalesOrderStatus, "status")
        if payment_status:
            update_data["payment_status"] = validate_enum(payment_status, SalesOrderPaymentStatus, "payment_status")

        total_value = _parse_decimal(total)
        if total_value is not None:
            update_data["total"] = total_value

        amount_paid_value = _parse_decimal(amount_paid)
        if amount_paid_value is not None:
            update_data["amount_paid"] = amount_paid_value

        paid_at_value = _parse_datetime(paid_at)
        if paid_at is not None:
            update_data["paid_at"] = paid_at_value

        if notes is not None:
            update_data["notes"] = notes.strip() or None

        # If payment status is paid and paid_at is missing, set it now to satisfy validation.
        if update_data.get("payment_status") == SalesOrderPaymentStatus.paid and update_data.get("paid_at") is None:
            update_data["paid_at"] = datetime.now(UTC)

        from app.schemas.sales_order import SalesOrderUpdate

        payload = SalesOrderUpdate(**update_data)
        return SalesOrders.update(db, sales_order_id, payload)

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
            data["amount"] = Decimal(data.get("quantity") or 0) * Decimal(data.get("unit_price") or 0)
        line = SalesOrderLine(**data)
        db.add(line)
        db.flush()
        _recalculate_order_totals(db, str(sales_order.id))
        db.commit()
        db.refresh(line)
        from app.services.events.handlers.splynx_customer import ensure_installation_invoice_for_sales_order

        ensure_installation_invoice_for_sales_order(db, sales_order.id)
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
        from app.services.events.handlers.splynx_customer import ensure_installation_invoice_for_sales_order

        ensure_installation_invoice_for_sales_order(db, line.sales_order_id)
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
            query = query.filter(SalesOrderLine.sales_order_id == coerce_uuid(sales_order_id))
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": SalesOrderLine.created_at},
        )
        return apply_pagination(query, limit, offset).all()


sales_orders = SalesOrders()
sales_order_lines = SalesOrderLines()
