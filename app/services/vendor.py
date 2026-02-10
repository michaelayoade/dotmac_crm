import html
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.network import FiberSegment, FiberSegmentType
from app.models.person import Person
from app.models.projects import Project
from app.models.qualification import BuildoutProject
from app.models.vendor import (
    AsBuiltRoute,
    AsBuiltRouteStatus,
    InstallationProject,
    InstallationProjectNote,
    InstallationProjectStatus,
    ProjectQuote,
    ProjectQuoteStatus,
    ProposedRouteRevision,
    ProposedRouteRevisionStatus,
    QuoteLineItem,
    Vendor,
    VendorAssignmentType,
)
from app.schemas.vendor import (
    AsBuiltRouteCreate,
    InstallationProjectCreate,
    InstallationProjectNoteCreate,
    InstallationProjectUpdate,
    ProjectQuoteCreate,
    ProjectQuoteUpdate,
    ProposedRouteRevisionCreate,
    QuoteLineItemCreate,
    VendorCreate,
    VendorUpdate,
)
from app.services import settings_spec
from app.services.common import apply_ordering, apply_pagination, coerce_uuid, round_money
from app.services.response import ListResponseMixin


def _now() -> datetime:
    return datetime.now(UTC)


def _ensure_vendor(db: Session, vendor_id: str) -> Vendor:
    vendor = db.get(Vendor, coerce_uuid(vendor_id))
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return vendor


def _ensure_person(db: Session, person_id: str) -> Person:
    person = db.get(Person, coerce_uuid(person_id))
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    return person


def _ensure_project(db: Session, project_id: str) -> Project:
    project = db.get(Project, coerce_uuid(project_id))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _ensure_buildout_project(db: Session, project_id: str) -> BuildoutProject:
    project = db.get(BuildoutProject, coerce_uuid(project_id))
    if not project:
        raise HTTPException(status_code=404, detail="Buildout project not found")
    return project


def _ensure_installation_project(db: Session, project_id: str) -> InstallationProject:
    project = db.get(InstallationProject, coerce_uuid(project_id))
    if not project:
        raise HTTPException(status_code=404, detail="Installation project not found")
    return project


def _ensure_quote(db: Session, quote_id: str) -> ProjectQuote:
    quote = db.get(ProjectQuote, coerce_uuid(quote_id))
    if not quote:
        raise HTTPException(status_code=404, detail="Project quote not found")
    return quote


def _ensure_route_revision(db: Session, revision_id: str) -> ProposedRouteRevision:
    revision = db.get(ProposedRouteRevision, coerce_uuid(revision_id))
    if not revision:
        raise HTTPException(status_code=404, detail="Route revision not found")
    return revision


def _ensure_as_built(db: Session, as_built_id: str) -> AsBuiltRoute:
    as_built = db.get(AsBuiltRoute, coerce_uuid(as_built_id))
    if not as_built:
        raise HTTPException(status_code=404, detail="As-built route not found")
    return as_built


def _geojson_to_geom(geojson: dict) -> object:
    geojson_str = json.dumps(geojson)
    return func.ST_SetSRID(func.ST_GeomFromGeoJSON(geojson_str), 4326)


def _get_route_geojson(db: Session, model, entity_id: str) -> dict | None:
    geojson_str = (
        db.query(func.ST_AsGeoJSON(model.route_geom))
        .filter(model.id == coerce_uuid(entity_id))
        .scalar()
    )
    if not geojson_str:
        return None
    return json.loads(geojson_str)


def _quote_total_from_items(db: Session, quote_id: str) -> Decimal:
    total = (
        db.query(func.coalesce(func.sum(QuoteLineItem.amount), 0))
        .filter(QuoteLineItem.quote_id == coerce_uuid(quote_id))
        .filter(QuoteLineItem.is_active.is_(True))
        .scalar()
    )
    return round_money(Decimal(total))


def _coerce_int(value: object | None, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        return int(value)
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _apply_validity_defaults(db: Session, quote: ProjectQuote) -> None:
    if quote.valid_from is None:
        quote.valid_from = _now()
    if quote.valid_until is None:
        days = settings_spec.resolve_value(
            db, SettingDomain.network, "vendor_quote_validity_days"
        )
        days = _coerce_int(days, 30)
        quote.valid_until = quote.valid_from + timedelta(days=days)


def check_approval_required(db: Session, quote: ProjectQuote) -> bool:
    threshold = settings_spec.resolve_value(
        db, SettingDomain.network, "vendor_quote_approval_threshold"
    )
    threshold_value = Decimal(str(threshold or "5000"))
    return Decimal(quote.total or Decimal("0.00")) > threshold_value


class Vendors(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: VendorCreate):
        vendor = Vendor(**payload.model_dump())
        db.add(vendor)
        db.commit()
        db.refresh(vendor)
        return vendor

    @staticmethod
    def get(db: Session, vendor_id: str):
        return _ensure_vendor(db, vendor_id)

    @staticmethod
    def list(
        db: Session,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(Vendor)
        if is_active is None:
            query = query.filter(Vendor.is_active.is_(True))
        else:
            query = query.filter(Vendor.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": Vendor.created_at, "name": Vendor.name},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, vendor_id: str, payload: VendorUpdate):
        vendor = _ensure_vendor(db, vendor_id)
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(vendor, key, value)
        db.commit()
        db.refresh(vendor)
        return vendor

    @staticmethod
    def delete(db: Session, vendor_id: str):
        vendor = _ensure_vendor(db, vendor_id)
        vendor.is_active = False
        db.commit()


class InstallationProjects(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: InstallationProjectCreate):
        _ensure_project(db, str(payload.project_id))
        if payload.buildout_project_id:
            _ensure_buildout_project(db, str(payload.buildout_project_id))
        if payload.assigned_vendor_id:
            _ensure_vendor(db, str(payload.assigned_vendor_id))
        if payload.created_by_person_id:
            _ensure_person(db, str(payload.created_by_person_id))
        data = payload.model_dump()
        if data.get("assigned_vendor_id") and not data.get("assignment_type"):
            data["assignment_type"] = VendorAssignmentType.direct
            data["status"] = InstallationProjectStatus.assigned
        project = InstallationProject(**data)
        db.add(project)
        db.commit()
        db.refresh(project)
        return project

    @staticmethod
    def get(db: Session, project_id: str):
        return _ensure_installation_project(db, project_id)

    @staticmethod
    def list(
        db: Session,
        status: str | None,
        vendor_id: str | None,
        subscriber_id: str | None,
        project_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(InstallationProject)
        if status:
            try:
                status_value = InstallationProjectStatus(status)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid status") from exc
            query = query.filter(InstallationProject.status == status_value)
        if vendor_id:
            query = query.filter(InstallationProject.assigned_vendor_id == coerce_uuid(vendor_id))
        if subscriber_id:
            query = query.filter(InstallationProject.subscriber_id == coerce_uuid(subscriber_id))
        if project_id:
            query = query.filter(InstallationProject.project_id == coerce_uuid(project_id))
        if is_active is None:
            query = query.filter(InstallationProject.is_active.is_(True))
        else:
            query = query.filter(InstallationProject.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": InstallationProject.created_at},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, project_id: str, payload: InstallationProjectUpdate):
        project = _ensure_installation_project(db, project_id)
        data = payload.model_dump(exclude_unset=True)
        if data.get("buildout_project_id"):
            _ensure_buildout_project(db, str(data["buildout_project_id"]))
        if data.get("assigned_vendor_id"):
            _ensure_vendor(db, str(data["assigned_vendor_id"]))
        for key, value in data.items():
            setattr(project, key, value)
        db.commit()
        db.refresh(project)
        return project

    @staticmethod
    def open_for_bidding(db: Session, project_id: str, bid_days: int | None):
        project = _ensure_installation_project(db, project_id)
        minimum_days = settings_spec.resolve_value(
            db, SettingDomain.network, "vendor_bid_minimum_days"
        )
        minimum_days = _coerce_int(minimum_days, 7)
        if bid_days is None:
            bid_days = minimum_days
        if bid_days < minimum_days:
            raise HTTPException(
                status_code=400,
                detail=f"Bid window must be at least {minimum_days} days",
            )
        project.assignment_type = VendorAssignmentType.bidding
        project.status = InstallationProjectStatus.open_for_bidding
        project.bidding_open_at = _now()
        project.bidding_close_at = _now() + timedelta(days=bid_days)
        db.commit()
        db.refresh(project)
        return project

    @staticmethod
    def assign_vendor(db: Session, project_id: str, vendor_id: str):
        project = _ensure_installation_project(db, project_id)
        _ensure_vendor(db, vendor_id)
        project.assignment_type = VendorAssignmentType.direct
        project.assigned_vendor_id = coerce_uuid(vendor_id)
        project.status = InstallationProjectStatus.assigned
        db.commit()
        db.refresh(project)
        return project

    @staticmethod
    def list_available_for_vendor(db: Session, vendor_id: str, limit: int, offset: int):
        _ensure_vendor(db, vendor_id)
        now = _now()
        query = (
            db.query(InstallationProject)
            .filter(InstallationProject.status == InstallationProjectStatus.open_for_bidding)
            .filter(InstallationProject.bidding_open_at <= now)
            .filter(InstallationProject.bidding_close_at >= now)
            .filter(InstallationProject.is_active.is_(True))
            .order_by(InstallationProject.bidding_close_at.asc())
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def list_for_vendor(db: Session, vendor_id: str, limit: int, offset: int):
        _ensure_vendor(db, vendor_id)
        query = (
            db.query(InstallationProject)
            .outerjoin(ProjectQuote, ProjectQuote.project_id == InstallationProject.id)
            .filter(
                (InstallationProject.assigned_vendor_id == coerce_uuid(vendor_id))
                | (ProjectQuote.vendor_id == coerce_uuid(vendor_id))
            )
            .filter(InstallationProject.is_active.is_(True))
            .distinct()
            .order_by(InstallationProject.updated_at.desc())
        )
        return apply_pagination(query, limit, offset).all()


class ProjectQuotes(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: ProjectQuoteCreate, vendor_id: str, created_by_person_id: str | None):
        project = _ensure_installation_project(db, str(payload.project_id))
        _ensure_vendor(db, vendor_id)
        if created_by_person_id:
            _ensure_person(db, created_by_person_id)
        if project.assignment_type == VendorAssignmentType.direct and str(project.assigned_vendor_id) != str(vendor_id):
            raise HTTPException(status_code=403, detail="Project is assigned to another vendor")
        if (
            project.status == InstallationProjectStatus.open_for_bidding
            and project.bidding_close_at
            and project.bidding_close_at <= _now()
        ):
            raise HTTPException(status_code=400, detail="Bidding window has closed")
        currency = settings_spec.resolve_value(
            db, SettingDomain.billing, "default_currency"
        ) or "NGN"
        quote = ProjectQuote(
            project_id=project.id,
            vendor_id=coerce_uuid(vendor_id),
            currency=currency,
            created_by_person_id=coerce_uuid(created_by_person_id) if created_by_person_id else None,
        )
        _apply_validity_defaults(db, quote)
        db.add(quote)
        db.commit()
        db.refresh(quote)
        return quote

    @staticmethod
    def get(db: Session, quote_id: str):
        return _ensure_quote(db, quote_id)

    @staticmethod
    def list(
        db: Session,
        project_id: str | None,
        vendor_id: str | None,
        status: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(ProjectQuote)
        if project_id:
            query = query.filter(ProjectQuote.project_id == coerce_uuid(project_id))
        if vendor_id:
            query = query.filter(ProjectQuote.vendor_id == coerce_uuid(vendor_id))
        if status:
            try:
                status_value = ProjectQuoteStatus(status)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid status") from exc
            query = query.filter(ProjectQuote.status == status_value)
        if is_active is None:
            query = query.filter(ProjectQuote.is_active.is_(True))
        else:
            query = query.filter(ProjectQuote.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": ProjectQuote.created_at, "total": ProjectQuote.total},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, quote_id: str, payload: ProjectQuoteUpdate):
        quote = _ensure_quote(db, quote_id)
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(quote, key, value)
        db.commit()
        db.refresh(quote)
        return quote

    @staticmethod
    def submit(db: Session, quote_id: str, vendor_id: str):
        quote = _ensure_quote(db, quote_id)
        if str(quote.vendor_id) != str(vendor_id):
            raise HTTPException(status_code=403, detail="Quote ownership required")
        if quote.status not in {ProjectQuoteStatus.draft, ProjectQuoteStatus.revision_requested}:
            raise HTTPException(status_code=400, detail="Quote is not in a submittable state")
        quote.status = ProjectQuoteStatus.submitted
        quote.submitted_at = _now()
        _apply_validity_defaults(db, quote)
        quote.subtotal = _quote_total_from_items(db, quote_id)
        quote.total = round_money(quote.subtotal + Decimal(quote.tax_total or Decimal("0.00")))
        if quote.project.status == InstallationProjectStatus.open_for_bidding:
            quote.project.status = InstallationProjectStatus.quoted
        db.commit()
        db.refresh(quote)
        return quote

    @staticmethod
    def approve(db: Session, quote_id: str, reviewer_person_id: str, review_notes: str | None, override: bool):
        quote = _ensure_quote(db, quote_id)
        _ensure_person(db, reviewer_person_id)
        quote.subtotal = _quote_total_from_items(db, quote_id)
        quote.total = round_money(quote.subtotal + Decimal(quote.tax_total or Decimal("0.00")))
        if check_approval_required(db, quote) and not override:
            raise HTTPException(
                status_code=400,
                detail="Approval threshold exceeded; escalation required",
            )
        quote.status = ProjectQuoteStatus.approved
        quote.reviewed_at = _now()
        quote.reviewed_by_person_id = coerce_uuid(reviewer_person_id)
        quote.review_notes = review_notes
        quote.project.approved_quote_id = quote.id
        quote.project.status = InstallationProjectStatus.approved
        db.commit()
        db.refresh(quote)
        return quote

    @staticmethod
    def reject(db: Session, quote_id: str, reviewer_person_id: str, review_notes: str | None):
        quote = _ensure_quote(db, quote_id)
        _ensure_person(db, reviewer_person_id)
        quote.status = ProjectQuoteStatus.rejected
        quote.reviewed_at = _now()
        quote.reviewed_by_person_id = coerce_uuid(reviewer_person_id)
        quote.review_notes = review_notes
        db.commit()
        db.refresh(quote)
        return quote


class QuoteLineItems(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: QuoteLineItemCreate, vendor_id: str | None = None):
        quote = _ensure_quote(db, str(payload.quote_id))
        if vendor_id and str(quote.vendor_id) != str(vendor_id):
            raise HTTPException(status_code=403, detail="Quote ownership required")
        data = payload.model_dump()
        quantity = Decimal(data.get("quantity") or Decimal("1.000"))
        unit_price = Decimal(data.get("unit_price") or Decimal("0.00"))
        data["amount"] = round_money(quantity * unit_price)
        item = QuoteLineItem(**data)
        db.add(item)
        db.commit()
        db.refresh(item)
        quote.subtotal = _quote_total_from_items(db, str(quote.id))
        quote.total = round_money(quote.subtotal + Decimal(quote.tax_total or Decimal("0.00")))
        db.commit()
        return item

    @staticmethod
    def list(
        db: Session,
        quote_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(QuoteLineItem)
        if quote_id:
            query = query.filter(QuoteLineItem.quote_id == coerce_uuid(quote_id))
        if is_active is None:
            query = query.filter(QuoteLineItem.is_active.is_(True))
        else:
            query = query.filter(QuoteLineItem.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": QuoteLineItem.created_at},
        )
        return apply_pagination(query, limit, offset).all()


class ProposedRouteRevisions(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: ProposedRouteRevisionCreate, vendor_id: str):
        quote = _ensure_quote(db, str(payload.quote_id))
        if str(quote.vendor_id) != str(vendor_id):
            raise HTTPException(status_code=403, detail="Quote ownership required")
        next_revision = (
            db.query(func.coalesce(func.max(ProposedRouteRevision.revision_number), 0))
            .filter(ProposedRouteRevision.quote_id == quote.id)
            .scalar()
        )
        revision_number = _coerce_int(next_revision, 0) + 1
        revision = ProposedRouteRevision(
            quote_id=quote.id,
            revision_number=revision_number,
            status=ProposedRouteRevisionStatus.draft,
            route_geom=_geojson_to_geom(payload.geojson),
            length_meters=payload.length_meters,
        )
        db.add(revision)
        db.commit()
        db.refresh(revision)
        return revision

    @staticmethod
    def create_for_admin(db: Session, quote_id: str, geojson: dict, length_meters: float | None):
        quote = _ensure_quote(db, str(quote_id))
        next_revision = (
            db.query(func.coalesce(func.max(ProposedRouteRevision.revision_number), 0))
            .filter(ProposedRouteRevision.quote_id == quote.id)
            .scalar()
        )
        revision_number = _coerce_int(next_revision, 0) + 1
        revision = ProposedRouteRevision(
            quote_id=quote.id,
            revision_number=revision_number,
            status=ProposedRouteRevisionStatus.draft,
            route_geom=_geojson_to_geom(geojson),
            length_meters=length_meters,
        )
        db.add(revision)
        db.commit()
        db.refresh(revision)
        return revision

    @staticmethod
    def submit(db: Session, revision_id: str, person_id: str, vendor_id: str | None = None):
        revision = _ensure_route_revision(db, revision_id)
        if vendor_id and str(revision.quote.vendor_id) != str(vendor_id):
            raise HTTPException(status_code=403, detail="Quote ownership required")
        _ensure_person(db, person_id)
        revision.status = ProposedRouteRevisionStatus.submitted
        revision.submitted_at = _now()
        revision.submitted_by_person_id = coerce_uuid(person_id)
        db.commit()
        db.refresh(revision)
        return revision

    @staticmethod
    def list(
        db: Session,
        quote_id: str | None,
        status: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(ProposedRouteRevision)
        if quote_id:
            query = query.filter(ProposedRouteRevision.quote_id == coerce_uuid(quote_id))
        if status:
            try:
                status_value = ProposedRouteRevisionStatus(status)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid status") from exc
            query = query.filter(ProposedRouteRevision.status == status_value)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": ProposedRouteRevision.created_at, "revision_number": ProposedRouteRevision.revision_number},
        )
        return apply_pagination(query, limit, offset).all()


class AsBuiltRoutes(ListResponseMixin):
    @staticmethod
    def _generate_report(as_built: AsBuiltRoute, reviewer_name: str | None) -> tuple[str, str]:
        project = as_built.project.project if as_built.project else None
        project_name = project.name if project else "Unknown Project"
        project_code = project.code if project and project.code else ""
        vendor_name = (
            as_built.project.assigned_vendor.name
            if as_built.project and as_built.project.assigned_vendor
            else "Unassigned"
        )
        submitted_by = (
            f"{as_built.submitted_by.first_name} {as_built.submitted_by.last_name}".strip()
            if as_built.submitted_by else "Unknown"
        )
        reviewed_by = reviewer_name or "Pending"
        report_title = f"As-Built Report - {project_name}"
        safe_title = html.escape(report_title)
        safe_project = html.escape(project_name)
        safe_vendor = html.escape(vendor_name)
        safe_submitted = html.escape(submitted_by)
        safe_reviewed = html.escape(reviewed_by)
        safe_code = html.escape(project_code)
        length_m = as_built.actual_length_meters or 0
        length_display = f"{length_m / 1000:.2f} km" if length_m >= 1000 else f"{length_m:.0f} m"
        safe_length = html.escape(length_display)
        safe_status = html.escape(as_built.status.value)

        html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>{safe_title}</title>
    <style>
        body {{ font-family: Arial, sans-serif; color: #0f172a; margin: 40px; }}
        h1 {{ font-size: 24px; margin-bottom: 4px; }}
        h2 {{ font-size: 16px; color: #475569; margin-top: 24px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
        th, td {{ text-align: left; padding: 8px 10px; border: 1px solid #e2e8f0; }}
        th {{ background: #f8fafc; font-weight: 600; }}
        .meta {{ color: #64748b; font-size: 12px; }}
    </style>
</head>
<body>
    <h1>{safe_project}</h1>
    <div class="meta">As-built report generated {_now().strftime("%Y-%m-%d %H:%M UTC")}</div>

    <h2>Summary</h2>
    <table>
        <tr><th>Project Code</th><td>{safe_code}</td></tr>
        <tr><th>Vendor</th><td>{safe_vendor}</td></tr>
        <tr><th>Status</th><td>{safe_status}</td></tr>
        <tr><th>As-built Length</th><td>{safe_length}</td></tr>
        <tr><th>Submitted By</th><td>{safe_submitted}</td></tr>
        <tr><th>Reviewed By</th><td>{safe_reviewed}</td></tr>
    </table>
</body>
</html>
"""
        filename = f"as_built_{as_built.id}.pdf"
        return filename, html_body

    @staticmethod
    def create(db: Session, payload: AsBuiltRouteCreate, vendor_id: str, submitted_by_person_id: str | None):
        project = _ensure_installation_project(db, str(payload.project_id))
        vendor = _ensure_vendor(db, vendor_id)
        approved_vendor_id = None
        if project.approved_quote_id:
            approved_quote = db.get(ProjectQuote, project.approved_quote_id)
            if approved_quote:
                approved_vendor_id = approved_quote.vendor_id
        if project.assigned_vendor_id and project.assigned_vendor_id != vendor.id:
            raise HTTPException(status_code=403, detail="Project is assigned to another vendor")
        if approved_vendor_id and approved_vendor_id != vendor.id:
            raise HTTPException(status_code=403, detail="Approved quote belongs to another vendor")
        if payload.proposed_revision_id:
            revision = _ensure_route_revision(db, str(payload.proposed_revision_id))
            if revision.quote.project_id != project.id:
                raise HTTPException(status_code=400, detail="Revision does not belong to project")
        as_built = AsBuiltRoute(
            project_id=project.id,
            proposed_revision_id=coerce_uuid(payload.proposed_revision_id) if payload.proposed_revision_id else None,
            route_geom=_geojson_to_geom(payload.geojson),
            actual_length_meters=payload.actual_length_meters,
            submitted_at=_now(),
            submitted_by_person_id=coerce_uuid(submitted_by_person_id) if submitted_by_person_id else None,
        )
        db.add(as_built)
        db.commit()
        db.refresh(as_built)
        return as_built

    @staticmethod
    def accept_and_convert(db: Session, as_built_id: str, reviewer_id: str):
        as_built = _ensure_as_built(db, as_built_id)
        reviewer = _ensure_person(db, reviewer_id)
        project_name = as_built.project.project.name
        project_code = as_built.project.project.code
        segment_name = f"Drop-{project_code or project_name}"
        segment = FiberSegment(
            name=segment_name,
            segment_type=FiberSegmentType.drop,
            route_geom=as_built.route_geom,
            length_m=as_built.actual_length_meters,
        )
        db.add(segment)
        db.flush()

        as_built.status = AsBuiltRouteStatus.accepted
        as_built.reviewed_at = _now()
        as_built.reviewed_by_person_id = coerce_uuid(reviewer_id)
        as_built.fiber_segment_id = segment.id
        as_built.project.status = InstallationProjectStatus.verified

        reviewer_name = f"{reviewer.first_name} {reviewer.last_name}".strip()
        filename, report_html = AsBuiltRoutes._generate_report(as_built, reviewer_name)
        report_dir = Path("uploads/as_built_reports")
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / filename
        try:
            from weasyprint import HTML
        except ImportError as exc:
            raise HTTPException(
                status_code=500,
                detail="WeasyPrint is not installed on the server. Install it to generate PDFs.",
            ) from exc
        HTML(string=report_html).write_pdf(str(report_path))
        as_built.report_file_path = str(report_path)
        as_built.report_file_name = filename
        as_built.report_generated_at = _now()

        db.commit()
        db.refresh(as_built)
        return as_built

    @staticmethod
    def get(db: Session, as_built_id: str):
        return _ensure_as_built(db, as_built_id)

    @staticmethod
    def list(
        db: Session,
        project_id: str | None,
        status: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(AsBuiltRoute)
        if project_id:
            query = query.filter(AsBuiltRoute.project_id == coerce_uuid(project_id))
        if status:
            try:
                status_value = AsBuiltRouteStatus(status)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid status") from exc
            query = query.filter(AsBuiltRoute.status == status_value)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": AsBuiltRoute.created_at},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def compare(db: Session, as_built_id: str) -> dict:
        as_built = _ensure_as_built(db, as_built_id)
        proposed_geojson = None
        if as_built.proposed_revision_id:
            proposed_geojson = _get_route_geojson(
                db, ProposedRouteRevision, str(as_built.proposed_revision_id)
            )
        as_built_geojson = _get_route_geojson(db, AsBuiltRoute, str(as_built.id))
        return {"proposed_geojson": proposed_geojson, "as_built_geojson": as_built_geojson}


class InstallationProjectNotes(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: InstallationProjectNoteCreate):
        _ensure_installation_project(db, str(payload.project_id))
        if payload.author_person_id:
            _ensure_person(db, str(payload.author_person_id))
        note = InstallationProjectNote(**payload.model_dump())
        db.add(note)
        db.commit()
        db.refresh(note)
        return note

    @staticmethod
    def list(
        db: Session,
        project_id: str | None,
        is_internal: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(InstallationProjectNote)
        if project_id:
            query = query.filter(InstallationProjectNote.project_id == coerce_uuid(project_id))
        if is_internal is not None:
            query = query.filter(InstallationProjectNote.is_internal == is_internal)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": InstallationProjectNote.created_at},
        )
        return apply_pagination(query, limit, offset).all()


vendors = Vendors()
installation_projects = InstallationProjects()
project_quotes = ProjectQuotes()
quote_line_items = QuoteLineItems()
proposed_route_revisions = ProposedRouteRevisions()
as_built_routes = AsBuiltRoutes()
installation_project_notes = InstallationProjectNotes()
