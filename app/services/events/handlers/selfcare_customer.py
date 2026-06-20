"""Event handler for creating selfcare customers on project creation."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.crm.sales import Lead
from app.models.person import Person
from app.models.projects import Project
from app.services.common import coerce_uuid
from app.services.events.types import Event, EventType
from app.services.selfcare import create_customer, ensure_person_customer, record_customer_sync_result
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


def _selfcare_subscriber_id(person: Person) -> str | None:
    metadata = person.metadata_ if isinstance(person.metadata_, dict) else {}
    value = str(metadata.get("selfcare_subscriber_id") or "").strip()
    return value or None


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
    existing_selfcare_id = _selfcare_subscriber_id(person)
    if existing_selfcare_id:
        ensure_person_customer(db, person, existing_selfcare_id)
        _ensure_subscriber(db, person, existing_selfcare_id, sales_order_id=sales_order_id)
        record_customer_sync_result(
            success=True,
            mode=mode,
            person_id=str(person.id),
            selfcare_subscriber_id=existing_selfcare_id,
            project_id=project_id,
            quote_id=quote_id,
            sales_order_id=sales_order_id,
            action="reused",
        )
        logger.info(
            "selfcare_skip_existing person_id=%s selfcare_subscriber_id=%s",
            person.id,
            existing_selfcare_id,
        )
        return existing_selfcare_id

    selfcare_id = create_customer(
        db,
        person,
        project_id=project_id,
        quote_id=quote_id,
        sales_order_id=sales_order_id,
    )
    if not selfcare_id:
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

    ensure_person_customer(db, person, selfcare_id)
    _ensure_subscriber(db, person, selfcare_id, sales_order_id=sales_order_id)
    record_customer_sync_result(
        success=True,
        mode=mode,
        person_id=str(person.id),
        selfcare_subscriber_id=selfcare_id,
        project_id=project_id,
        quote_id=quote_id,
        sales_order_id=sales_order_id,
        action="created",
    )
    logger.info(
        "selfcare_customer_created person_id=%s selfcare_subscriber_id=%s",
        person.id,
        selfcare_id,
    )
    return selfcare_id


def _ensure_subscriber(
    db: Session,
    person: Person,
    selfcare_subscriber_id: str,
    *,
    sales_order_id: str | None = None,
) -> None:
    data: dict = {
        "person_id": person.id,
        "organization_id": person.organization_id,
        "status": "pending",
        "subscriber_number": selfcare_subscriber_id,
    }
    if sales_order_id:
        try:
            data["sales_order_id"] = coerce_uuid(sales_order_id)
        except Exception:
            logger.warning("selfcare_invalid_sales_order_id value=%s - skipping", sales_order_id)

    subscriber_service.sync_from_external(db, "selfcare", selfcare_subscriber_id, data)
