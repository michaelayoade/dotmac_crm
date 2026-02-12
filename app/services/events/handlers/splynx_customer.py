"""Event handler for creating Splynx customers on project creation."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.crm.sales import Lead
from app.models.person import Person
from app.models.projects import Project
from app.services.common import coerce_uuid
from app.services.events.types import Event, EventType
from app.services.splynx import create_customer, ensure_person_customer
from app.services.subscriber import subscriber as subscriber_service

logger = logging.getLogger(__name__)


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
            ensure_person_customer(db, person, person.splynx_id)
            _ensure_subscriber(
                db,
                person,
                person.splynx_id,
                sales_order_id=sales_order_id,
            )
            logger.info(
                "splynx_skip_existing person_id=%s splynx_id=%s",
                person.id,
                person.splynx_id,
            )
            return

        splynx_id = create_customer(db, person)
        if not splynx_id:
            return

        ensure_person_customer(db, person, splynx_id)
        _ensure_subscriber(db, person, splynx_id, sales_order_id=sales_order_id)
        logger.info(
            "splynx_customer_created person_id=%s splynx_id=%s",
            person.id,
            splynx_id,
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


def _resolve_sales_order_id(project: Project) -> str | None:
    """Extract sales_order_id from project metadata (set during quote acceptance)."""
    if not project.metadata_ or not isinstance(project.metadata_, dict):
        return None
    return project.metadata_.get("sales_order_id")


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
