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
