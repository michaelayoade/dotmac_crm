import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.vendor import ProposedRouteRevisionStatus
from app.schemas.vendor import (
    InstallationProjectCreate,
    ProjectQuoteCreate,
    ProposedRouteRevisionCreate,
    QuoteLineItemCreate,
    VendorCreate,
)
from app.services import vendor as vendor_service


def _build_submittable_quote(db_session, project, person):
    vendor = vendor_service.vendors.create(db_session, VendorCreate(name="Acme Vendor"))
    installation_project = vendor_service.installation_projects.create(
        db_session,
        InstallationProjectCreate(
            project_id=project.id,
            assigned_vendor_id=vendor.id,
        ),
    )
    quote = vendor_service.project_quotes.create(
        db_session,
        ProjectQuoteCreate(project_id=installation_project.id),
        vendor_id=str(vendor.id),
        created_by_person_id=str(person.id),
    )
    vendor_service.quote_line_items.create(
        db_session,
        QuoteLineItemCreate(
            quote_id=quote.id,
            item_type="labor",
            description="Route planning labor",
            quantity=Decimal("1.000"),
            unit_price=Decimal("1000.00"),
        ),
        vendor_id=str(vendor.id),
    )
    vendor_service.project_quotes.submit(db_session, str(quote.id), vendor_id=str(vendor.id))
    return vendor, quote


def test_route_revision_approve_links_created_segment(db_session, project, person, monkeypatch):
    monkeypatch.setattr(vendor_service, "_geojson_to_geom", lambda _geojson: None)

    vendor, quote = _build_submittable_quote(db_session, project, person)
    revision = vendor_service.proposed_route_revisions.create(
        db_session,
        ProposedRouteRevisionCreate(
            quote_id=quote.id,
            geojson={"type": "LineString", "coordinates": [[3.0, 6.0], [3.001, 6.001]]},
            length_meters=157.25,
        ),
        vendor_id=str(vendor.id),
    )

    vendor_service.proposed_route_revisions.submit(
        db_session,
        revision_id=str(revision.id),
        person_id=str(person.id),
        vendor_id=str(vendor.id),
    )

    fake_segment = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(
        vendor_service.ProposedRouteRevisions,
        "_replace_segment_for_revision",
        staticmethod(lambda _db, _revision: fake_segment),
    )

    approved = vendor_service.proposed_route_revisions.approve(
        db_session,
        revision_id=str(revision.id),
        reviewer_person_id=str(person.id),
    )

    assert approved.status == ProposedRouteRevisionStatus.accepted
    assert approved.fiber_segment_id == fake_segment.id


def test_route_revision_approve_requires_submitted_state(db_session, project, person, monkeypatch):
    monkeypatch.setattr(vendor_service, "_geojson_to_geom", lambda _geojson: None)

    vendor, quote = _build_submittable_quote(db_session, project, person)
    revision = vendor_service.proposed_route_revisions.create(
        db_session,
        ProposedRouteRevisionCreate(
            quote_id=quote.id,
            geojson={"type": "LineString", "coordinates": [[3.0, 6.0], [3.001, 6.001]]},
            length_meters=157.25,
        ),
        vendor_id=str(vendor.id),
    )

    with pytest.raises(HTTPException) as exc:
        vendor_service.proposed_route_revisions.approve(
            db_session,
            revision_id=str(revision.id),
            reviewer_person_id=str(person.id),
        )

    assert exc.value.status_code == 400
    assert "Only submitted route revisions can be approved" in str(exc.value.detail)


def test_find_duplicate_segments_returns_empty_when_no_route_geometry(db_session, project, person, monkeypatch):
    monkeypatch.setattr(vendor_service, "_geojson_to_geom", lambda _geojson: None)

    vendor, quote = _build_submittable_quote(db_session, project, person)
    revision = vendor_service.proposed_route_revisions.create(
        db_session,
        ProposedRouteRevisionCreate(
            quote_id=quote.id,
            geojson={"type": "LineString", "coordinates": [[3.0, 6.0], [3.001, 6.001]]},
            length_meters=157.25,
        ),
        vendor_id=str(vendor.id),
    )

    warnings = vendor_service.proposed_route_revisions.find_duplicate_segments(
        db_session,
        revision_id=str(revision.id),
    )
    assert warnings == []
