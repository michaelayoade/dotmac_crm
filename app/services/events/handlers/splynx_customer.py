"""Event handler for creating Splynx customers on project creation."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.crm.sales import CrmQuoteLineItem, Lead
from app.models.person import Person
from app.models.projects import Project
from app.models.sales_order import SalesOrderLine
from app.services.common import coerce_uuid
from app.services.events.types import Event, EventType
from app.services.splynx import (
    create_customer,
    create_installation_invoice,
    ensure_person_customer,
    fetch_customer,
)
from app.services.subscriber import subscriber as subscriber_service

logger = logging.getLogger(__name__)


def _is_valid_splynx_id(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, int):
        return value > 0
    if not isinstance(value, str):
        return False
    cleaned = value.strip()
    return bool(cleaned) and cleaned.isdigit()


def _clear_invalid_splynx_id(person_id: object) -> None:
    """Remove invalid splynx_id from person metadata in an isolated transaction.

    Uses its own session so the cleanup survives even if the calling handler
    is rolled back by the event dispatcher.
    """
    from app.db import SessionLocal

    isolated = SessionLocal()
    try:
        person = isolated.get(Person, person_id)
        if person and isinstance(person.metadata_, dict) and "splynx_id" in person.metadata_:
            person.metadata_ = {k: v for k, v in person.metadata_.items() if k != "splynx_id"}
            isolated.add(person)
            isolated.commit()
    except Exception:
        isolated.rollback()
        logger.warning("splynx_clear_invalid_id_failed person_id=%s", person_id, exc_info=True)
    finally:
        isolated.close()


class SplynxCustomerHandler:
    """Create Splynx customers when projects are created."""

    def handle(self, db: Session, event: Event) -> None:
        if event.event_type != EventType.project_created:
            return

        project_id = event.project_id or event.payload.get("project_id")
        if not project_id:
            return

        project = db.get(Project, project_id)
        if not project:
            return

        person = _resolve_person_for_project(db, project)
        if not person:
            logger.info("splynx_skip_no_person project_id=%s", project_id)
            return

        sales_order_id = _resolve_sales_order_id(project)

        if person.splynx_id:
            existing_splynx_id = str(person.splynx_id).strip()
            if _is_valid_splynx_id(existing_splynx_id):
                ensure_person_customer(db, person, existing_splynx_id)
                _ensure_subscriber(
                    db,
                    person,
                    existing_splynx_id,
                    sales_order_id=sales_order_id,
                )
                _ensure_installation_invoice(db, project, existing_splynx_id)
                logger.info(
                    "splynx_skip_existing person_id=%s splynx_id=%s",
                    person.id,
                    existing_splynx_id,
                )
                return

            if existing_splynx_id:
                logger.warning(
                    "splynx_invalid_existing_id person_id=%s splynx_id=%s; clearing and creating new customer",
                    person.id,
                    person.splynx_id,
                )
                # Clear the invalid value using an isolated session so the
                # cleanup persists even if create_customer() fails and the
                # dispatcher rolls back the handler's shared session.
                _clear_invalid_splynx_id(person.id)

        splynx_id = create_customer(db, person)
        if not splynx_id:
            return

        ensure_person_customer(db, person, splynx_id)
        _ensure_subscriber(db, person, splynx_id, sales_order_id=sales_order_id)
        _ensure_installation_invoice(db, project, splynx_id)
        logger.info(
            "splynx_customer_created person_id=%s splynx_id=%s",
            person.id,
            splynx_id,
        )


def ensure_installation_invoice_for_sales_order(db: Session, sales_order_id: object) -> None:
    """Best-effort retry for manual sales-order flows where lines are added after project creation."""
    if not sales_order_id:
        return

    project = (
        db.query(Project)
        .filter(Project.is_active.is_(True))
        .filter(func.json_extract_path_text(Project.metadata_, "sales_order_id") == str(sales_order_id))
        .order_by(Project.created_at.desc())
        .first()
    )
    if not project:
        return

    person = _resolve_person_for_project(db, project)
    if not person:
        return

    splynx_id = str(person.splynx_id or "").strip()
    if not _is_valid_splynx_id(splynx_id):
        return

    if not fetch_customer(db, splynx_id):
        logger.warning(
            "splynx_existing_id_not_found person_id=%s splynx_id=%s; creating new customer",
            person.id,
            splynx_id,
        )
        new_splynx_id = create_customer(db, person)
        if not new_splynx_id:
            return
        ensure_person_customer(db, person, new_splynx_id)
        splynx_id = str(new_splynx_id)

    _ensure_installation_invoice(db, project, splynx_id)


def _resolve_person_for_project(db: Session, project: Project) -> Person | None:
    if project.subscriber and project.subscriber.person:
        return project.subscriber.person
    if project.owner_person_id:
        person = db.get(Person, project.owner_person_id)
        if person:
            return person
    if project.lead_id:
        lead = db.get(Lead, project.lead_id)
        if lead and lead.person:
            return lead.person
    return None


def _resolve_sales_order_id(project: Project) -> str | None:
    """Extract sales_order_id from project metadata (set during quote acceptance)."""
    if not project.metadata_ or not isinstance(project.metadata_, dict):
        return None
    return project.metadata_.get("sales_order_id")


def _resolve_quote_id(project: Project) -> str | None:
    """Extract quote_id from project metadata (set during quote acceptance)."""
    if not project.metadata_ or not isinstance(project.metadata_, dict):
        return None
    return project.metadata_.get("quote_id")


def _ensure_installation_invoice(db: Session, project: Project, splynx_id: str) -> None:
    if not splynx_id:
        return
    if _has_existing_installation_invoice(project):
        return
    related_invoice = _find_existing_related_installation_invoice(db, project)
    if related_invoice:
        invoice_id, amount = related_invoice
        _store_invoice_metadata(project, invoice_id, amount)
        db.add(project)
        db.commit()
        db.refresh(project)
        logger.info(
            "splynx_installation_invoice_reused project_id=%s invoice_id=%s",
            project.id,
            invoice_id,
        )
        return

    amount = _resolve_installation_amount(db, project)
    if amount <= 0:
        logger.info("splynx_invoice_skip_no_installation_cost project_id=%s", project.id)
        return

    new_invoice_id: str | None = create_installation_invoice(
        db,
        splynx_id=splynx_id,
        amount=amount,
        description="Installation cost",
        external_ref=f"project:{project.id}",
    )
    if not new_invoice_id:
        return

    _store_invoice_metadata(project, new_invoice_id, amount)
    db.add(project)
    db.commit()
    db.refresh(project)
    logger.info(
        "splynx_installation_invoice_created project_id=%s splynx_id=%s invoice_id=%s amount=%s",
        project.id,
        splynx_id,
        new_invoice_id,
        amount,
    )


def _has_existing_installation_invoice(project: Project) -> bool:
    metadata = project.metadata_ if isinstance(project.metadata_, dict) else {}
    invoice_id = metadata.get("splynx_installation_invoice_id")
    return bool(str(invoice_id or "").strip())


def _find_existing_related_installation_invoice(db: Session, project: Project) -> tuple[str, Decimal | None] | None:
    sales_order_id = _resolve_sales_order_id(project)
    quote_id = _resolve_quote_id(project)
    if not sales_order_id and not quote_id:
        return None

    filters = []
    if sales_order_id:
        filters.append(Project.metadata_["sales_order_id"].as_string() == str(sales_order_id))
    if quote_id:
        filters.append(Project.metadata_["quote_id"].as_string() == str(quote_id))

    if filters:
        rows = (
            db.query(Project)
            .filter(Project.id != project.id)
            .filter(or_(*filters))
            .order_by(Project.created_at.desc())
            .all()
        )
        for row in rows:
            metadata = row.metadata_ if isinstance(row.metadata_, dict) else {}
            invoice_id = str(metadata.get("splynx_installation_invoice_id") or "").strip()
            if invoice_id:
                amount = _parse_invoice_amount(metadata.get("splynx_installation_invoice_amount"))
                return invoice_id, amount

    # SQLite JSON path comparisons are not reliable across SQLAlchemy/SQLite builds.
    # Fall back to an in-Python metadata check to keep this idempotent in tests/dev.
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "sqlite":
        rows = db.query(Project).filter(Project.id != project.id).all()
        for row in rows:
            metadata = row.metadata_ if isinstance(row.metadata_, dict) else {}
            same_sales_order = sales_order_id and str(metadata.get("sales_order_id")) == str(sales_order_id)
            same_quote = quote_id and str(metadata.get("quote_id")) == str(quote_id)
            if not (same_sales_order or same_quote):
                continue
            invoice_id = str(metadata.get("splynx_installation_invoice_id") or "").strip()
            if invoice_id:
                amount = _parse_invoice_amount(metadata.get("splynx_installation_invoice_amount"))
                return invoice_id, amount
    return None


def _store_invoice_metadata(project: Project, invoice_id: str, amount: Decimal | None) -> None:
    metadata = dict(project.metadata_ or {})
    metadata["splynx_installation_invoice_id"] = str(invoice_id)
    if amount is not None:
        metadata["splynx_installation_invoice_amount"] = str(amount)
    project.metadata_ = metadata


def _parse_invoice_amount(value: object) -> Decimal | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _resolve_installation_amount(db: Session, project: Project) -> Decimal:
    sales_order_id = _resolve_sales_order_id(project)
    amount_from_sales_order = _installation_amount_from_sales_order(db, sales_order_id)
    if amount_from_sales_order > 0:
        return amount_from_sales_order
    quote_id = _resolve_quote_id(project)
    return _installation_amount_from_quote(db, quote_id)


def _installation_amount_from_sales_order(db: Session, sales_order_id: str | None) -> Decimal:
    if not sales_order_id:
        return Decimal("0.00")
    try:
        sales_order_uuid = coerce_uuid(sales_order_id)
    except Exception:
        logger.warning("splynx_invoice_invalid_sales_order_id value=%s", sales_order_id)
        return Decimal("0.00")

    lines = (
        db.query(SalesOrderLine)
        .filter(SalesOrderLine.sales_order_id == sales_order_uuid)
        .filter(SalesOrderLine.is_active.is_(True))
        .all()
    )
    return _sum_installation_lines(lines)


def _installation_amount_from_quote(db: Session, quote_id: str | None) -> Decimal:
    if not quote_id:
        return Decimal("0.00")
    try:
        quote_uuid = coerce_uuid(quote_id)
    except Exception:
        logger.warning("splynx_invoice_invalid_quote_id value=%s", quote_id)
        return Decimal("0.00")
    lines = db.query(CrmQuoteLineItem).filter(CrmQuoteLineItem.quote_id == quote_uuid).all()
    return _sum_installation_lines(lines)


def _sum_installation_lines(lines: Sequence[object]) -> Decimal:
    total = Decimal("0.00")
    for line in lines:
        description = str(getattr(line, "description", "") or "").lower()
        if "installation" not in description:
            continue
        amount = Decimal(getattr(line, "amount", 0) or 0)
        if amount > 0:
            total += amount
    return total


def _ensure_subscriber(
    db: Session,
    person: Person,
    splynx_id: str,
    *,
    sales_order_id: str | None = None,
) -> None:
    """Ensure a local Subscriber record exists for the Splynx customer (idempotent)."""
    data: dict = {
        "person_id": person.id,
        "organization_id": person.organization_id,
        "status": "active",
        "subscriber_number": splynx_id,
    }
    if sales_order_id:
        try:
            data["sales_order_id"] = coerce_uuid(sales_order_id)
        except Exception:
            logging.getLogger(__name__).warning("splynx_invalid_sales_order_id value=%s — skipping", sales_order_id)

    subscriber_service.sync_from_external(db, "splynx", splynx_id, data)
