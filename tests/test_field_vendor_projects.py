"""Tests for vendor crew project endpoints in the field API."""

import uuid

import pytest
from fastapi import HTTPException

from app.models.subscriber import Subscriber
from app.models.vendor import (
    AsBuiltRouteStatus,
    InstallationProject,
    InstallationProjectStatus,
    ProjectQuote,
    ProjectQuoteStatus,
    Vendor,
    VendorPurchaseInvoice,
    VendorPurchaseInvoiceStatus,
)
from app.schemas.vendor import AsBuiltRouteCreate
from app.services.field.vendor_projects import field_vendor_projects


@pytest.fixture(autouse=True)
def _no_postgis(monkeypatch):
    """SQLite has no ST_GeomFromGeoJSON; geometry persistence is covered by PG envs."""
    from app.services import vendor as vendor_service

    monkeypatch.setattr(vendor_service, "_geojson_to_geom", lambda _geojson: None)


@pytest.fixture()
def vendor(db_session):
    vendor = Vendor(name="FiberWorks Ltd", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    db_session.refresh(vendor)
    return vendor


@pytest.fixture()
def other_vendor(db_session):
    vendor = Vendor(name="Rival Crew Co", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    db_session.refresh(vendor)
    return vendor


@pytest.fixture()
def installation_project(db_session, vendor, project):
    ip = InstallationProject(
        project_id=project.id,
        assigned_vendor_id=vendor.id,
        status=InstallationProjectStatus.in_progress,
    )
    db_session.add(ip)
    db_session.commit()
    db_session.refresh(ip)
    return ip


@pytest.fixture()
def submitted_quote(db_session, vendor, installation_project):
    quote = ProjectQuote(
        project_id=installation_project.id,
        vendor_id=vendor.id,
        status=ProjectQuoteStatus.submitted,
    )
    db_session.add(quote)
    db_session.commit()
    return quote


def _geojson():
    return {
        "type": "LineString",
        "coordinates": [[3.4216, 6.4281], [3.4225, 6.4290], [3.4233, 6.4301]],
    }


def test_list_mine_scoped_to_vendor(db_session, vendor, other_vendor, installation_project):
    mine = field_vendor_projects.list_mine(db_session, str(vendor.id))
    assert [p.id for p in mine] == [installation_project.id]
    assert field_vendor_projects.list_mine(db_session, str(other_vendor.id)) == []


def test_detail_404_for_other_vendor(db_session, other_vendor, installation_project):
    with pytest.raises(HTTPException) as exc:
        field_vendor_projects.get_detail(db_session, str(other_vendor.id), str(installation_project.id))
    assert exc.value.status_code == 404


def test_submit_as_built(db_session, vendor, person, installation_project, submitted_quote):
    route = field_vendor_projects.submit_as_built(
        db_session,
        str(vendor.id),
        str(person.id),
        str(installation_project.id),
        AsBuiltRouteCreate(
            project_id=installation_project.id,
            geojson=_geojson(),
            actual_length_meters=212.5,
        ),
    )
    assert route.project_id == installation_project.id
    assert route.status == AsBuiltRouteStatus.submitted


def test_submit_requires_submitted_quote(db_session, vendor, person, installation_project):
    with pytest.raises(HTTPException) as exc:
        field_vendor_projects.submit_as_built(
            db_session,
            str(vendor.id),
            str(person.id),
            str(installation_project.id),
            AsBuiltRouteCreate(project_id=installation_project.id, geojson=_geojson()),
        )
    assert exc.value.status_code == 403


def test_payload_project_mismatch_rejected(db_session, vendor, person, installation_project, submitted_quote):
    with pytest.raises(HTTPException) as exc:
        field_vendor_projects.submit_as_built(
            db_session,
            str(vendor.id),
            str(person.id),
            str(installation_project.id),
            AsBuiltRouteCreate(project_id=uuid.uuid4(), geojson=_geojson()),
        )
    assert exc.value.status_code == 422


def test_rejected_submission_offered_for_resubmission(
    db_session, vendor, person, installation_project, submitted_quote
):
    route = field_vendor_projects.submit_as_built(
        db_session,
        str(vendor.id),
        str(person.id),
        str(installation_project.id),
        AsBuiltRouteCreate(project_id=installation_project.id, geojson=_geojson(), actual_length_meters=100.0),
    )
    route.status = AsBuiltRouteStatus.rejected
    db_session.commit()

    bundle = field_vendor_projects.get_detail(db_session, str(vendor.id), str(installation_project.id))
    assert bundle["rejected_for_resubmission"] is not None
    assert bundle["rejected_for_resubmission"].id == route.id

    # A newer submission supersedes the rejected one.
    field_vendor_projects.submit_as_built(
        db_session,
        str(vendor.id),
        str(person.id),
        str(installation_project.id),
        AsBuiltRouteCreate(project_id=installation_project.id, geojson=_geojson(), actual_length_meters=105.0),
    )
    bundle = field_vendor_projects.get_detail(db_session, str(vendor.id), str(installation_project.id))
    assert bundle["rejected_for_resubmission"] is None
    assert len(bundle["submissions"]) == 2


def test_detail_has_no_site_without_subscriber(db_session, vendor, installation_project):
    """A buildout-only project with no subscriber yields site=None, not an error."""
    bundle = field_vendor_projects.get_detail(db_session, str(vendor.id), str(installation_project.id))
    assert bundle["site"] is None


def test_detail_site_bundle_from_subscriber(db_session, vendor, person, project):
    """The vendor project detail surfaces who-to-call + where-to-go from the
    linked subscriber, reusing the technician contact/address helpers."""
    sub = Subscriber(
        person_id=person.id,
        external_system="selfcare",
        external_id=uuid.uuid4().hex[:8],
        service_address_line1="12 Fiber Way",
        service_city="Lagos",
        service_plan="Business 500M",
        account_number="ACC-9001",
    )
    db_session.add(sub)
    db_session.commit()
    ip = InstallationProject(
        project_id=project.id,
        assigned_vendor_id=vendor.id,
        subscriber_id=sub.id,
        status=InstallationProjectStatus.in_progress,
        notes="Gate code 4455; ask for the site foreman.",
    )
    db_session.add(ip)
    db_session.commit()

    bundle = field_vendor_projects.get_detail(db_session, str(vendor.id), str(ip.id))
    site = bundle["site"]
    assert site is not None
    assert site["subscriber_id"] == sub.id
    assert site["email"] == person.email
    assert site["address_text"] == "12 Fiber Way, Lagos"
    assert site["service_plan"] == "Business 500M"
    assert site["account_number"] == "ACC-9001"
    assert site["access_notes"] == "Gate code 4455; ask for the site foreman."
    assert site["additional_contacts"] == []
    assert site["recent_visits"] == []


def test_list_detailed_includes_quote_lifecycle(db_session, vendor, installation_project, submitted_quote):
    """The job list carries each project's lifecycle state (bid/approval)."""
    from decimal import Decimal

    submitted_quote.total = Decimal("150000.00")
    db_session.commit()

    items = field_vendor_projects.list_mine_detailed(db_session, str(vendor.id))
    assert len(items) == 1
    lc = items[0]["lifecycle"]
    assert lc["quote"]["status"] == "submitted"
    assert lc["quote"]["total"] == 150000.0
    assert lc["as_built"] is None
    assert lc["billing"] is None


def test_detail_lifecycle_tracks_as_built_and_billing(
    db_session, vendor, person, installation_project, submitted_quote
):
    """After an as-built and an invoice exist, the detail lifecycle reflects both."""
    field_vendor_projects.submit_as_built(
        db_session,
        str(vendor.id),
        str(person.id),
        str(installation_project.id),
        AsBuiltRouteCreate(project_id=installation_project.id, geojson=_geojson(), actual_length_meters=100.0),
    )
    invoice = VendorPurchaseInvoice(
        project_id=installation_project.id,
        vendor_id=vendor.id,
        invoice_number="PINV-2001",
        status=VendorPurchaseInvoiceStatus.submitted,
    )
    db_session.add(invoice)
    db_session.commit()

    bundle = field_vendor_projects.get_detail(db_session, str(vendor.id), str(installation_project.id))
    lc = bundle["lifecycle"]
    assert lc["quote"]["status"] == "submitted"
    assert lc["as_built"]["status"] == "submitted"
    assert lc["billing"]["status"] == "submitted"
    assert lc["billing"]["invoice_number"] == "PINV-2001"
    assert lc["billing"]["erp_synced"] is False


def test_staff_token_rejected_on_vendor_routes(db_session, person):
    """require_vendor_token guards the routes: no VendorUser → 403."""
    from app.services.vendor_auth_tokens import require_vendor_token

    auth = {"person_id": str(person.id), "session_id": "s", "roles": [], "scopes": []}
    with pytest.raises(HTTPException) as exc:
        require_vendor_token(auth=auth, db=db_session)
    assert exc.value.status_code == 403


def _walk(dependant):
    for dep in dependant.dependencies:
        yield dep
        yield from _walk(dep)


def test_routes_use_vendor_token_guard():
    from fastapi.routing import APIRoute

    from app.api.field.vendor_projects import router
    from app.services.vendor_auth_tokens import require_vendor_token

    routes = [r for r in router.routes if isinstance(r, APIRoute)]
    assert len(routes) == 3
    for route in routes:
        found = any(dep.call is require_vendor_token for dep in _walk(route.dependant))
        assert found, f"{route.path} missing require_vendor_token"
