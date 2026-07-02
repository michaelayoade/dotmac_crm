"""Vendor crew project views for the field app.

Wraps the existing vendor service (listing, as-built creation incl. the
quote-submitted guard) — this layer only adds the bearer-token caller scoping
and the resubmission context the mobile capture flow needs.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.field import FieldAttachment
from app.models.vendor import AsBuiltRoute, AsBuiltRouteStatus, InstallationProject
from app.schemas.vendor import AsBuiltRouteCreate
from app.services import vendor as vendor_service
from app.services.common import apply_pagination, coerce_uuid


def _scoped_project(db: Session, vendor_id: str, project_id: str) -> InstallationProject:
    project = db.get(InstallationProject, coerce_uuid(project_id))
    if not project or str(project.assigned_vendor_id) != str(coerce_uuid(vendor_id)):
        # Same 404 for missing and foreign projects: existence must not leak.
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _site_bundle(db: Session, project: InstallationProject) -> dict | None:
    """Customer + site context so a crew knows who to call and where to go.

    Resolves from the project's linked subscriber, reusing the technician-side
    contact/address helpers so both flows share one shape. Returns ``None`` for
    projects with no subscriber (e.g. pure buildout work). Access notes come
    from the project's own ``notes`` — a vendor project has no work order.
    """
    subscriber = project.subscriber
    if subscriber is None:
        return None
    # Imported here to avoid pulling the technician job module (and its
    # WorkOrder/Ticket imports) into the vendor path at module load.
    from app.services.field.jobs import (
        _additional_contacts,
        _best_phone,
        _recent_visits,
        _site_address,
    )

    person = subscriber.person
    return {
        "subscriber_id": subscriber.id,
        "name": (person.display_name or f"{person.first_name} {person.last_name}".strip()) if person else None,
        "phone": _best_phone(person),
        "email": person.email if person else None,
        "address_text": _site_address(subscriber, person),
        "service_plan": subscriber.service_plan,
        "account_number": subscriber.account_number,
        "status": subscriber.status.value if getattr(subscriber, "status", None) else None,
        "access_notes": project.notes,
        "additional_contacts": _additional_contacts(subscriber),
        # No work order to exclude for a vendor project → pass a null id.
        "recent_visits": _recent_visits(db, subscriber, None),
    }


class FieldVendorProjects:
    @staticmethod
    def list_mine(db: Session, vendor_id: str, *, limit: int = 50, offset: int = 0) -> list[InstallationProject]:
        return vendor_service.installation_projects.list_for_vendor(db, str(vendor_id), limit, offset)

    @staticmethod
    def get_detail(db: Session, vendor_id: str, project_id: str) -> dict:
        project = _scoped_project(db, vendor_id, project_id)
        submissions = (
            db.query(AsBuiltRoute)
            .filter(AsBuiltRoute.project_id == project.id)
            .order_by(AsBuiltRoute.submitted_at.desc().nullslast(), AsBuiltRoute.created_at.desc())
            .all()
        )
        attachments = (
            db.query(FieldAttachment)
            .filter(FieldAttachment.installation_project_id == project.id)
            .filter(FieldAttachment.is_active.is_(True))
            .order_by(FieldAttachment.created_at.desc())
            .all()
        )
        # Resubmission pre-fill: the latest rejected route, if no newer
        # submission superseded it.
        latest = submissions[0] if submissions else None
        rejected_for_resubmission = (
            latest if latest is not None and latest.status == AsBuiltRouteStatus.rejected else None
        )
        return {
            "project": project,
            "site": _site_bundle(db, project),
            "submissions": submissions,
            "attachments": attachments,
            "rejected_for_resubmission": rejected_for_resubmission,
        }

    @staticmethod
    def submit_as_built(
        db: Session,
        vendor_id: str,
        person_id: str,
        project_id: str,
        payload: AsBuiltRouteCreate,
    ) -> AsBuiltRoute:
        project = _scoped_project(db, vendor_id, project_id)
        if str(payload.project_id) != str(project.id):
            raise HTTPException(status_code=422, detail="Payload project does not match URL")
        # Same business guard the vendor web portal enforces.
        if not vendor_service.project_quotes.has_submitted_for_vendor_project(db, str(project.id), str(vendor_id)):
            raise HTTPException(status_code=403, detail="Quote must be submitted before as-built can be provided")
        return vendor_service.as_built_routes.create(
            db,
            payload,
            vendor_id=str(vendor_id),
            submitted_by_person_id=str(person_id),
        )

    @staticmethod
    def paginate(db: Session, query, limit: int, offset: int):
        return apply_pagination(query, limit, offset).all()


field_vendor_projects = FieldVendorProjects()
