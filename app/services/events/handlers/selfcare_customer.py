"""Event handler for creating selfcare customers on project creation."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.crm.sales import CrmQuoteLineItem, Lead
from app.models.person import Person
from app.models.projects import Project
from app.models.sales_order import SalesOrderLine
from app.services import selfcare
from app.services.common import coerce_uuid
from app.services.events.types import Event, EventType
from app.services.selfcare import (
    SelfcareCustomerIdentity,
    create_customer,
    ensure_person_customer,
    record_customer_sync_result,
)
from app.services.subscriber import subscriber as subscriber_service

logger = logging.getLogger(__name__)


class SelfcareCustomerHandler:
    """Create selfcare customers when quote-driven projects are created."""

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
            logger.info("selfcare_skip_no_person project_id=%s", project_id)
            return

        metadata = project.metadata_ if isinstance(project.metadata_, dict) else {}
        sales_order_id = str(metadata.get("sales_order_id") or "").strip() or None
        quote_id = str(metadata.get("quote_id") or "").strip() or None

        sync_person_to_selfcare(
            db,
            person,
            project_id=str(project.id),
            quote_id=quote_id,
            sales_order_id=sales_order_id,
            mode="event",
        )
        _ensure_installation_invoice(db, project, person)


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


def _selfcare_identity(person: Person) -> SelfcareCustomerIdentity | None:
    metadata = person.metadata_ if isinstance(person.metadata_, dict) else {}
    selfcare_id = str(metadata.get("selfcare_id") or "").strip() or None
    subscriber_number = str(metadata.get("selfcare_subscriber_id") or "").strip()
    if not subscriber_number and selfcare_id:
        subscriber_number = selfcare_id
    if not subscriber_number:
        return None
    return SelfcareCustomerIdentity(selfcare_id=selfcare_id, subscriber_number=subscriber_number)


def sync_person_to_selfcare(
    db: Session,
    person: Person,
    *,
    project_id: str | None = None,
    quote_id: str | None = None,
    sales_order_id: str | None = None,
    mode: str = "manual",
) -> str | None:
    """Create/reuse a selfcare customer for a CRM person and record sync history."""
    existing_identity = _selfcare_identity(person)
    if existing_identity:
        ensure_person_customer(db, person, existing_identity)
        _ensure_subscriber(db, person, existing_identity, sales_order_id=sales_order_id)
        record_customer_sync_result(
            success=True,
            mode=mode,
            person_id=str(person.id),
            selfcare_id=existing_identity.selfcare_id,
            selfcare_subscriber_id=existing_identity.subscriber_number,
            project_id=project_id,
            quote_id=quote_id,
            sales_order_id=sales_order_id,
            action="reused",
        )
        logger.info(
            "selfcare_skip_existing person_id=%s selfcare_subscriber_id=%s",
            person.id,
            existing_identity.subscriber_number,
        )
        return existing_identity.subscriber_number

    identity = create_customer(
        db,
        person,
        project_id=project_id,
        quote_id=quote_id,
        sales_order_id=sales_order_id,
    )
    if not identity:
        record_customer_sync_result(
            success=False,
            mode=mode,
            person_id=str(person.id),
            project_id=project_id,
            quote_id=quote_id,
            sales_order_id=sales_order_id,
            action="create_failed",
            error="Selfcare customer creation failed or sync is not configured.",
        )
        return None

    ensure_person_customer(db, person, identity)
    _ensure_subscriber(db, person, identity, sales_order_id=sales_order_id)
    record_customer_sync_result(
        success=True,
        mode=mode,
        person_id=str(person.id),
        selfcare_id=identity.selfcare_id,
        selfcare_subscriber_id=identity.subscriber_number,
        project_id=project_id,
        quote_id=quote_id,
        sales_order_id=sales_order_id,
        action="created",
    )
    logger.info(
        "selfcare_customer_created person_id=%s selfcare_subscriber_id=%s",
        person.id,
        identity.subscriber_number,
    )
    return identity.subscriber_number


def _ensure_subscriber(
    db: Session,
    person: Person,
    identity: SelfcareCustomerIdentity,
    *,
    sales_order_id: str | None = None,
) -> None:
    data: dict = {
        "person_id": person.id,
        "organization_id": person.organization_id,
        "status": "pending",
        "subscriber_number": identity.subscriber_number,
        "sync_metadata": {
            "selfcare_uuid": identity.selfcare_id,
            "selfcare_subscriber_number": identity.subscriber_number,
        },
    }
    if sales_order_id:
        try:
            data["sales_order_id"] = coerce_uuid(sales_order_id)
        except Exception:
            logger.warning("selfcare_invalid_sales_order_id value=%s - skipping", sales_order_id)

    subscriber_service.sync_from_external(db, "selfcare", identity.external_id, data)


# ---------------------------------------------------------------------------
# Installation invoices (ported from the decommissioned Splynx handler; now
# created in dotmac_sub via the selfcare client).
# ---------------------------------------------------------------------------


def ensure_installation_invoice_for_sales_order(db: Session, sales_order_id: object) -> None:
    """Best-effort retry for manual sales-order flows where lines are added
    after project creation. Creates the dotmac_sub installation invoice."""
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

    if not _selfcare_identity(person):
        # Customer not yet created in selfcare — create it now, then invoice.
        sync_person_to_selfcare(
            db,
            person,
            project_id=str(project.id),
            sales_order_id=str(sales_order_id),
            mode="sales_order_retry",
        )
        if not _selfcare_identity(person):
            return

    _ensure_installation_invoice(db, project, person)


def _ensure_installation_invoice(db: Session, project: Project, person: Person) -> None:
    identity = _selfcare_identity(person)
    subscriber_id = identity.external_id if identity else None
    if not subscriber_id:
        return
    # Serialize concurrent triggers (the project_created event AND sales-order line
    # create/update both call this) so the read-then-create-then-store sequence
    # can't double-create an invoice. Re-read the project state under the lock.
    # populate_existing() forces the locked row to refresh already-loaded column
    # attributes, so the existence check below reads committed state under the lock,
    # not a stale in-session copy.
    locked = db.query(Project).filter(Project.id == project.id).with_for_update().populate_existing().first()
    if locked is None:
        return
    project = locked
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
            "selfcare_installation_invoice_reused project_id=%s invoice_id=%s",
            project.id,
            invoice_id,
        )
        return

    amount = _resolve_installation_amount(db, project)
    if amount <= 0:
        logger.info("selfcare_invoice_skip_no_installation_cost project_id=%s", project.id)
        return

    try:
        new_invoice_id = selfcare.create_installation_invoice(
            db,
            subscriber_id=subscriber_id,
            amount=amount,
            description="Installation cost",
            external_ref=f"project:{project.id}",
        )
    except selfcare.SelfcareProviderError as exc:
        # Don't silently drop the invoice — record the failure so it surfaces and
        # a later trigger (or an operator) can retry. The sub-app external_ref dedup
        # makes the retry safe.
        _record_invoice_failure(project, str(exc))
        db.add(project)
        db.commit()
        logger.error("selfcare_installation_invoice_failed project_id=%s error=%s", project.id, exc)
        return
    if not new_invoice_id:
        return

    _store_invoice_metadata(project, new_invoice_id, amount)
    db.add(project)
    db.commit()
    db.refresh(project)
    logger.info(
        "selfcare_installation_invoice_created project_id=%s subscriber_id=%s invoice_id=%s amount=%s",
        project.id,
        subscriber_id,
        new_invoice_id,
        amount,
    )


def _resolve_sales_order_id(project: Project) -> str | None:
    if not isinstance(project.metadata_, dict):
        return None
    return project.metadata_.get("sales_order_id")


def _resolve_quote_id(project: Project) -> str | None:
    if not isinstance(project.metadata_, dict):
        return None
    return project.metadata_.get("quote_id")


def _has_existing_installation_invoice(project: Project) -> bool:
    metadata = project.metadata_ if isinstance(project.metadata_, dict) else {}
    return bool(str(metadata.get("selfcare_installation_invoice_id") or "").strip())


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
            invoice_id = str(metadata.get("selfcare_installation_invoice_id") or "").strip()
            if invoice_id:
                return invoice_id, _parse_invoice_amount(metadata.get("selfcare_installation_invoice_amount"))

    # SQLite JSON path comparisons are unreliable; fall back to an in-Python
    # check to keep this idempotent in tests/dev.
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "sqlite":
        rows = db.query(Project).filter(Project.id != project.id).all()
        for row in rows:
            metadata = row.metadata_ if isinstance(row.metadata_, dict) else {}
            same_sales_order = sales_order_id and str(metadata.get("sales_order_id")) == str(sales_order_id)
            same_quote = quote_id and str(metadata.get("quote_id")) == str(quote_id)
            if not (same_sales_order or same_quote):
                continue
            invoice_id = str(metadata.get("selfcare_installation_invoice_id") or "").strip()
            if invoice_id:
                return invoice_id, _parse_invoice_amount(metadata.get("selfcare_installation_invoice_amount"))
    return None


def _store_invoice_metadata(project: Project, invoice_id: str, amount: Decimal | None) -> None:
    metadata = dict(project.metadata_ or {})
    metadata["selfcare_installation_invoice_id"] = str(invoice_id)
    if amount is not None:
        metadata["selfcare_installation_invoice_amount"] = str(amount)
    metadata.pop("selfcare_installation_invoice_error", None)
    project.metadata_ = metadata


def _record_invoice_failure(project: Project, detail: str) -> None:
    metadata = dict(project.metadata_ or {})
    metadata["selfcare_installation_invoice_error"] = {
        "detail": detail[:500],
        "at": datetime.now(UTC).isoformat(),
    }
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
    amount_from_sales_order = _installation_amount_from_sales_order(db, _resolve_sales_order_id(project))
    if amount_from_sales_order > 0:
        return amount_from_sales_order
    return _installation_amount_from_quote(db, _resolve_quote_id(project))


def _installation_amount_from_sales_order(db: Session, sales_order_id: str | None) -> Decimal:
    if not sales_order_id:
        return Decimal("0.00")
    try:
        sales_order_uuid = coerce_uuid(sales_order_id)
    except Exception:
        logger.warning("selfcare_invoice_invalid_sales_order_id value=%s", sales_order_id)
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
        logger.warning("selfcare_invoice_invalid_quote_id value=%s", quote_id)
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
