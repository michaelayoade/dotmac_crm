"""Tests for vendor services."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.models.vendor import (
    InstallationProjectStatus,
    ProjectQuoteStatus,
    ProposedRouteRevisionStatus,
    AsBuiltRouteStatus,
    VendorAssignmentType,
    Vendor,
    InstallationProject,
    ProjectQuote,
    QuoteLineItem,
)
from app.schemas.vendor import (
    VendorCreate,
    VendorUpdate,
    InstallationProjectCreate,
    InstallationProjectUpdate,
    ProjectQuoteCreate,
    ProjectQuoteUpdate,
    QuoteLineItemCreate,
    ProposedRouteRevisionCreate,
    AsBuiltRouteCreate,
    InstallationProjectNoteCreate,
)
from app.services import vendor as vendor_service


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture()
def vendor(db_session):
    """Create a test vendor."""
    vendor = vendor_service.Vendors.create(
        db_session,
        VendorCreate(
            name="Test Fiber Installers",
            code="TFI001",
            contact_name="John Smith",
            contact_email="john@testfiber.com",
            contact_phone="+1-555-1234",
        ),
    )
    return vendor


@pytest.fixture()
def vendor2(db_session):
    """Create a second vendor for multi-vendor tests."""
    vendor = vendor_service.Vendors.create(
        db_session,
        VendorCreate(
            name="Quick Install Co",
            code="QIC002",
        ),
    )
    return vendor


@pytest.fixture()
def installation_project(db_session, project, vendor):
    """Create an installation project linked to a vendor."""
    ip = vendor_service.InstallationProjects.create(
        db_session,
        InstallationProjectCreate(
            project_id=project.id,
            assigned_vendor_id=vendor.id,
        ),
    )
    return ip


@pytest.fixture()
def installation_project_bidding(db_session, project):
    """Create an installation project open for bidding."""
    # Create a new base project first
    from app.schemas.projects import ProjectCreate
    from app.services import projects as projects_service

    base_project = projects_service.projects.create(
        db_session,
        ProjectCreate(name="Bidding Test Project"),
    )

    ip = vendor_service.InstallationProjects.create(
        db_session,
        InstallationProjectCreate(project_id=base_project.id),
    )
    # Open for bidding
    with patch.object(vendor_service.settings_spec, "resolve_value", return_value="7"):
        ip = vendor_service.InstallationProjects.open_for_bidding(
            db_session, str(ip.id), bid_days=10
        )
    return ip


@pytest.fixture()
def project_quote(db_session, installation_project, vendor, person):
    """Create a project quote."""
    quote = vendor_service.ProjectQuotes.create(
        db_session,
        ProjectQuoteCreate(project_id=installation_project.id),
        vendor_id=str(vendor.id),
        created_by_person_id=str(person.id),
    )
    return quote


@pytest.fixture()
def quote_with_line_items(db_session, project_quote, vendor):
    """Create quote with line items for total calculation tests."""
    vendor_service.QuoteLineItems.create(
        db_session,
        QuoteLineItemCreate(
            quote_id=project_quote.id,
            item_type="fiber",
            description="Fiber cable 100m",
            quantity=Decimal("100.000"),
            unit_price=Decimal("1.50"),
        ),
        vendor_id=str(vendor.id),
    )
    vendor_service.QuoteLineItems.create(
        db_session,
        QuoteLineItemCreate(
            quote_id=project_quote.id,
            item_type="labor",
            description="Installation labor",
            quantity=Decimal("8.000"),
            unit_price=Decimal("50.00"),
        ),
        vendor_id=str(vendor.id),
    )
    db_session.refresh(project_quote)
    return project_quote


# =============================================================================
# Vendors CRUD Tests
# =============================================================================


def test_create_vendor(db_session):
    """Test creating a vendor."""
    vendor = vendor_service.Vendors.create(
        db_session,
        VendorCreate(
            name="New Fiber Co",
            code="NFC001",
            contact_name="Jane Doe",
            contact_email="jane@newfiber.com",
            license_number="LIC-12345",
            service_area="Metro Area",
            notes="Specializes in residential installations",
        ),
    )
    assert vendor.name == "New Fiber Co"
    assert vendor.code == "NFC001"
    assert vendor.contact_name == "Jane Doe"
    assert vendor.license_number == "LIC-12345"
    assert vendor.is_active is True


def test_get_vendor(db_session, vendor):
    """Test getting a vendor by ID."""
    fetched = vendor_service.Vendors.get(db_session, str(vendor.id))
    assert fetched.id == vendor.id
    assert fetched.name == vendor.name


def test_get_vendor_not_found(db_session):
    """Test getting a non-existent vendor raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.Vendors.get(db_session, str(uuid.uuid4()))
    assert exc_info.value.status_code == 404
    assert "Vendor not found" in exc_info.value.detail


def test_list_vendors(db_session, vendor, vendor2):
    """Test listing vendors."""
    vendors = vendor_service.Vendors.list(
        db_session,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(vendors) >= 2


def test_list_vendors_active_only(db_session, vendor):
    """Test listing only active vendors (default behavior)."""
    vendors = vendor_service.Vendors.list(
        db_session,
        is_active=None,  # None means active only
        order_by="name",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert all(v.is_active for v in vendors)


def test_list_vendors_inactive_only(db_session, vendor):
    """Test listing only inactive vendors."""
    # Deactivate vendor
    vendor_service.Vendors.delete(db_session, str(vendor.id))

    vendors = vendor_service.Vendors.list(
        db_session,
        is_active=False,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert any(v.id == vendor.id for v in vendors)


def test_update_vendor(db_session, vendor):
    """Test updating a vendor."""
    updated = vendor_service.Vendors.update(
        db_session,
        str(vendor.id),
        VendorUpdate(
            name="Updated Fiber Co",
            contact_phone="+1-555-9999",
        ),
    )
    assert updated.name == "Updated Fiber Co"
    assert updated.contact_phone == "+1-555-9999"


def test_update_vendor_not_found(db_session):
    """Test updating a non-existent vendor raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.Vendors.update(
            db_session, str(uuid.uuid4()), VendorUpdate(name="New Name")
        )
    assert exc_info.value.status_code == 404


def test_delete_vendor(db_session, vendor):
    """Test soft-deleting a vendor."""
    vendor_service.Vendors.delete(db_session, str(vendor.id))
    db_session.refresh(vendor)
    assert vendor.is_active is False


def test_delete_vendor_not_found(db_session):
    """Test deleting a non-existent vendor raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.Vendors.delete(db_session, str(uuid.uuid4()))
    assert exc_info.value.status_code == 404


# =============================================================================
# Installation Projects Tests
# =============================================================================


def test_create_installation_project(db_session, project):
    """Test creating an installation project."""
    ip = vendor_service.InstallationProjects.create(
        db_session,
        InstallationProjectCreate(
            project_id=project.id,
            notes="New fiber drop installation",
        ),
    )
    assert ip.project_id == project.id
    assert ip.status == InstallationProjectStatus.draft
    assert ip.is_active is True


def test_create_installation_project_with_vendor(db_session, project, vendor):
    """Test creating installation project with direct vendor assignment."""
    ip = vendor_service.InstallationProjects.create(
        db_session,
        InstallationProjectCreate(
            project_id=project.id,
            assigned_vendor_id=vendor.id,
        ),
    )
    assert ip.assigned_vendor_id == vendor.id
    assert ip.assignment_type == VendorAssignmentType.direct
    assert ip.status == InstallationProjectStatus.assigned


def test_create_installation_project_invalid_project(db_session):
    """Test creating installation project with invalid project raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.InstallationProjects.create(
            db_session,
            InstallationProjectCreate(project_id=uuid.uuid4()),
        )
    assert exc_info.value.status_code == 404
    assert "Project not found" in exc_info.value.detail


def test_create_installation_project_invalid_vendor(db_session, project):
    """Test creating installation project with invalid vendor raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.InstallationProjects.create(
            db_session,
            InstallationProjectCreate(
                project_id=project.id,
                assigned_vendor_id=uuid.uuid4(),
            ),
        )
    assert exc_info.value.status_code == 404
    assert "Vendor not found" in exc_info.value.detail


def test_create_installation_project_with_person(db_session, project, person):
    """Test creating installation project with created_by person."""
    ip = vendor_service.InstallationProjects.create(
        db_session,
        InstallationProjectCreate(
            project_id=project.id,
            created_by_person_id=person.id,
        ),
    )
    assert ip.created_by_person_id == person.id


def test_create_installation_project_invalid_person(db_session, project):
    """Test creating installation project with invalid person raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.InstallationProjects.create(
            db_session,
            InstallationProjectCreate(
                project_id=project.id,
                created_by_person_id=uuid.uuid4(),
            ),
        )
    assert exc_info.value.status_code == 404
    assert "Person not found" in exc_info.value.detail


def test_get_installation_project(db_session, installation_project):
    """Test getting an installation project by ID."""
    fetched = vendor_service.InstallationProjects.get(
        db_session, str(installation_project.id)
    )
    assert fetched.id == installation_project.id


def test_get_installation_project_not_found(db_session):
    """Test getting a non-existent installation project raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.InstallationProjects.get(db_session, str(uuid.uuid4()))
    assert exc_info.value.status_code == 404
    assert "Installation project not found" in exc_info.value.detail


def test_list_installation_projects(db_session, installation_project):
    """Test listing installation projects."""
    projects = vendor_service.InstallationProjects.list(
        db_session,
        status=None,
        vendor_id=None,
        account_id=None,
        project_id=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(projects) >= 1


def test_list_installation_projects_by_status(db_session, installation_project):
    """Test listing installation projects by status."""
    projects = vendor_service.InstallationProjects.list(
        db_session,
        status="assigned",
        vendor_id=None,
        account_id=None,
        project_id=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert all(p.status == InstallationProjectStatus.assigned for p in projects)


def test_list_installation_projects_invalid_status(db_session):
    """Test listing with invalid status raises 400."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.InstallationProjects.list(
            db_session,
            status="invalid_status",
            vendor_id=None,
            account_id=None,
            project_id=None,
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=10,
            offset=0,
        )
    assert exc_info.value.status_code == 400
    assert "Invalid status" in exc_info.value.detail


def test_list_installation_projects_by_vendor(db_session, installation_project, vendor):
    """Test listing installation projects by vendor."""
    projects = vendor_service.InstallationProjects.list(
        db_session,
        status=None,
        vendor_id=str(vendor.id),
        account_id=None,
        project_id=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert all(p.assigned_vendor_id == vendor.id for p in projects)


def test_update_installation_project(db_session, installation_project):
    """Test updating an installation project."""
    updated = vendor_service.InstallationProjects.update(
        db_session,
        str(installation_project.id),
        InstallationProjectUpdate(notes="Updated notes"),
    )
    assert updated.notes == "Updated notes"


def test_update_installation_project_not_found(db_session):
    """Test updating a non-existent installation project raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.InstallationProjects.update(
            db_session,
            str(uuid.uuid4()),
            InstallationProjectUpdate(notes="New"),
        )
    assert exc_info.value.status_code == 404


def test_open_for_bidding(db_session, project):
    """Test opening an installation project for bidding."""
    ip = vendor_service.InstallationProjects.create(
        db_session,
        InstallationProjectCreate(project_id=project.id),
    )

    with patch.object(vendor_service.settings_spec, "resolve_value", return_value="7"):
        opened = vendor_service.InstallationProjects.open_for_bidding(
            db_session, str(ip.id), bid_days=10
        )

    assert opened.status == InstallationProjectStatus.open_for_bidding
    assert opened.assignment_type == VendorAssignmentType.bidding
    assert opened.bidding_open_at is not None
    assert opened.bidding_close_at is not None


def test_open_for_bidding_default_days(db_session, project):
    """Test opening for bidding with default days from settings."""
    ip = vendor_service.InstallationProjects.create(
        db_session,
        InstallationProjectCreate(project_id=project.id),
    )

    with patch.object(vendor_service.settings_spec, "resolve_value", return_value="14"):
        opened = vendor_service.InstallationProjects.open_for_bidding(
            db_session, str(ip.id), bid_days=None
        )

    assert opened.bidding_close_at is not None


def test_open_for_bidding_below_minimum(db_session, project):
    """Test opening for bidding with days below minimum raises 400."""
    ip = vendor_service.InstallationProjects.create(
        db_session,
        InstallationProjectCreate(project_id=project.id),
    )

    with patch.object(vendor_service.settings_spec, "resolve_value", return_value="7"):
        with pytest.raises(HTTPException) as exc_info:
            vendor_service.InstallationProjects.open_for_bidding(
                db_session, str(ip.id), bid_days=3
            )
    assert exc_info.value.status_code == 400
    assert "at least" in exc_info.value.detail


def test_assign_vendor(db_session, installation_project_bidding, vendor):
    """Test directly assigning a vendor to a project."""
    assigned = vendor_service.InstallationProjects.assign_vendor(
        db_session, str(installation_project_bidding.id), str(vendor.id)
    )
    assert assigned.assigned_vendor_id == vendor.id
    assert assigned.assignment_type == VendorAssignmentType.direct
    assert assigned.status == InstallationProjectStatus.assigned


def test_assign_vendor_not_found(db_session, installation_project_bidding):
    """Test assigning a non-existent vendor raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.InstallationProjects.assign_vendor(
            db_session, str(installation_project_bidding.id), str(uuid.uuid4())
        )
    assert exc_info.value.status_code == 404


def test_list_available_for_vendor(db_session, installation_project_bidding, vendor):
    """Test listing projects available for vendor bidding."""
    projects = vendor_service.InstallationProjects.list_available_for_vendor(
        db_session, str(vendor.id), limit=10, offset=0
    )
    assert any(p.id == installation_project_bidding.id for p in projects)


def test_list_available_for_vendor_not_found(db_session):
    """Test listing available with invalid vendor raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.InstallationProjects.list_available_for_vendor(
            db_session, str(uuid.uuid4()), limit=10, offset=0
        )
    assert exc_info.value.status_code == 404


def test_list_for_vendor(db_session, installation_project, vendor):
    """Test listing projects for a specific vendor."""
    projects = vendor_service.InstallationProjects.list_for_vendor(
        db_session, str(vendor.id), limit=10, offset=0
    )
    assert any(p.id == installation_project.id for p in projects)


def test_list_for_vendor_not_found(db_session):
    """Test listing for invalid vendor raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.InstallationProjects.list_for_vendor(
            db_session, str(uuid.uuid4()), limit=10, offset=0
        )
    assert exc_info.value.status_code == 404


# =============================================================================
# Project Quotes Tests
# =============================================================================


def test_create_project_quote(db_session, installation_project, vendor, person):
    """Test creating a project quote."""
    def mock_resolve_value(db, domain, key):
        if key == "default_currency":
            return "USD"
        if key == "vendor_quote_validity_days":
            return "30"
        return None

    with patch.object(
        vendor_service.settings_spec, "resolve_value", side_effect=mock_resolve_value
    ):
        quote = vendor_service.ProjectQuotes.create(
            db_session,
            ProjectQuoteCreate(project_id=installation_project.id),
            vendor_id=str(vendor.id),
            created_by_person_id=str(person.id),
        )

    assert quote.project_id == installation_project.id
    assert quote.vendor_id == vendor.id
    assert quote.status == ProjectQuoteStatus.draft
    assert quote.valid_from is not None
    assert quote.valid_until is not None


def test_create_project_quote_wrong_vendor(db_session, installation_project, vendor2):
    """Test creating quote by wrong vendor raises 403."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.ProjectQuotes.create(
            db_session,
            ProjectQuoteCreate(project_id=installation_project.id),
            vendor_id=str(vendor2.id),
            created_by_person_id=None,
        )
    assert exc_info.value.status_code == 403
    assert "another vendor" in exc_info.value.detail


def test_create_project_quote_bidding_closed(db_session, project, vendor):
    """Test creating quote after bidding window closes raises 400."""
    # Create a new project for this test
    from app.schemas.projects import ProjectCreate
    from app.services import projects as projects_service

    base_project = projects_service.projects.create(
        db_session,
        ProjectCreate(name="Closed Bidding Test"),
    )
    ip = vendor_service.InstallationProjects.create(
        db_session,
        InstallationProjectCreate(project_id=base_project.id),
    )

    # Open for bidding - use minimal bid days
    with patch.object(vendor_service.settings_spec, "resolve_value", return_value="1"):
        ip = vendor_service.InstallationProjects.open_for_bidding(
            db_session, str(ip.id), bid_days=1
        )

    db_session.refresh(ip)

    # Get the bidding close time and create a future time
    # Use naive datetime to match SQLite behavior (remove tzinfo)
    future_time = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)

    with patch.object(vendor_service, "_now", return_value=future_time):
        with pytest.raises(HTTPException) as exc_info:
            vendor_service.ProjectQuotes.create(
                db_session,
                ProjectQuoteCreate(project_id=ip.id),
                vendor_id=str(vendor.id),
                created_by_person_id=None,
            )
    assert exc_info.value.status_code == 400
    assert "closed" in exc_info.value.detail


def test_get_project_quote(db_session, project_quote):
    """Test getting a project quote by ID."""
    fetched = vendor_service.ProjectQuotes.get(db_session, str(project_quote.id))
    assert fetched.id == project_quote.id


def test_get_project_quote_not_found(db_session):
    """Test getting a non-existent quote raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.ProjectQuotes.get(db_session, str(uuid.uuid4()))
    assert exc_info.value.status_code == 404
    assert "Project quote not found" in exc_info.value.detail


def test_list_project_quotes(db_session, project_quote):
    """Test listing project quotes."""
    quotes = vendor_service.ProjectQuotes.list(
        db_session,
        project_id=None,
        vendor_id=None,
        status=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(quotes) >= 1


def test_list_project_quotes_by_status(db_session, project_quote):
    """Test listing quotes by status."""
    quotes = vendor_service.ProjectQuotes.list(
        db_session,
        project_id=None,
        vendor_id=None,
        status="draft",
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert all(q.status == ProjectQuoteStatus.draft for q in quotes)


def test_list_project_quotes_invalid_status(db_session):
    """Test listing with invalid status raises 400."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.ProjectQuotes.list(
            db_session,
            project_id=None,
            vendor_id=None,
            status="invalid",
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=10,
            offset=0,
        )
    assert exc_info.value.status_code == 400


def test_update_project_quote(db_session, project_quote):
    """Test updating a project quote."""
    updated = vendor_service.ProjectQuotes.update(
        db_session,
        str(project_quote.id),
        ProjectQuoteUpdate(review_notes="Updated notes"),
    )
    assert updated.review_notes == "Updated notes"


def test_update_project_quote_not_found(db_session):
    """Test updating a non-existent quote raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.ProjectQuotes.update(
            db_session,
            str(uuid.uuid4()),
            ProjectQuoteUpdate(review_notes="Notes"),
        )
    assert exc_info.value.status_code == 404


def test_submit_project_quote(db_session, quote_with_line_items, vendor):
    """Test submitting a project quote."""
    submitted = vendor_service.ProjectQuotes.submit(
        db_session, str(quote_with_line_items.id), str(vendor.id)
    )
    assert submitted.status == ProjectQuoteStatus.submitted
    assert submitted.submitted_at is not None
    # subtotal = 100*1.50 + 8*50 = 150 + 400 = 550
    assert submitted.subtotal == Decimal("550.00")
    assert submitted.total == Decimal("550.00")


def test_submit_project_quote_wrong_vendor(db_session, project_quote, vendor2):
    """Test submitting quote by wrong vendor raises 403."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.ProjectQuotes.submit(
            db_session, str(project_quote.id), str(vendor2.id)
        )
    assert exc_info.value.status_code == 403
    assert "ownership" in exc_info.value.detail


def test_submit_project_quote_already_submitted(db_session, quote_with_line_items, vendor):
    """Test submitting an already submitted quote raises 400."""
    vendor_service.ProjectQuotes.submit(
        db_session, str(quote_with_line_items.id), str(vendor.id)
    )

    with pytest.raises(HTTPException) as exc_info:
        vendor_service.ProjectQuotes.submit(
            db_session, str(quote_with_line_items.id), str(vendor.id)
        )
    assert exc_info.value.status_code == 400
    assert "submittable" in exc_info.value.detail


def test_approve_project_quote(db_session, quote_with_line_items, vendor, person):
    """Test approving a project quote."""
    # First submit
    vendor_service.ProjectQuotes.submit(
        db_session, str(quote_with_line_items.id), str(vendor.id)
    )

    with patch.object(vendor_service.settings_spec, "resolve_value", return_value="10000"):
        approved = vendor_service.ProjectQuotes.approve(
            db_session,
            str(quote_with_line_items.id),
            reviewer_person_id=str(person.id),
            review_notes="Looks good",
            override=False,
        )

    assert approved.status == ProjectQuoteStatus.approved
    assert approved.reviewed_at is not None
    assert approved.reviewed_by_person_id == person.id
    assert approved.project.approved_quote_id == approved.id
    assert approved.project.status == InstallationProjectStatus.approved


def test_approve_project_quote_exceeds_threshold(db_session, quote_with_line_items, vendor, person):
    """Test approving quote that exceeds threshold without override raises 400."""
    vendor_service.ProjectQuotes.submit(
        db_session, str(quote_with_line_items.id), str(vendor.id)
    )

    with patch.object(vendor_service.settings_spec, "resolve_value", return_value="100"):
        with pytest.raises(HTTPException) as exc_info:
            vendor_service.ProjectQuotes.approve(
                db_session,
                str(quote_with_line_items.id),
                reviewer_person_id=str(person.id),
                review_notes=None,
                override=False,
            )
    assert exc_info.value.status_code == 400
    assert "threshold exceeded" in exc_info.value.detail


def test_approve_project_quote_with_override(db_session, quote_with_line_items, vendor, person):
    """Test approving quote with override bypasses threshold check."""
    vendor_service.ProjectQuotes.submit(
        db_session, str(quote_with_line_items.id), str(vendor.id)
    )

    with patch.object(vendor_service.settings_spec, "resolve_value", return_value="100"):
        approved = vendor_service.ProjectQuotes.approve(
            db_session,
            str(quote_with_line_items.id),
            reviewer_person_id=str(person.id),
            review_notes="Override approved by manager",
            override=True,
        )

    assert approved.status == ProjectQuoteStatus.approved


def test_reject_project_quote(db_session, project_quote, person):
    """Test rejecting a project quote."""
    rejected = vendor_service.ProjectQuotes.reject(
        db_session,
        str(project_quote.id),
        reviewer_person_id=str(person.id),
        review_notes="Price too high",
    )
    assert rejected.status == ProjectQuoteStatus.rejected
    assert rejected.reviewed_at is not None
    assert rejected.review_notes == "Price too high"


def test_reject_project_quote_invalid_reviewer(db_session, project_quote):
    """Test rejecting quote with invalid reviewer raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.ProjectQuotes.reject(
            db_session,
            str(project_quote.id),
            reviewer_person_id=str(uuid.uuid4()),
            review_notes=None,
        )
    assert exc_info.value.status_code == 404


# =============================================================================
# Quote Line Items Tests
# =============================================================================


def test_create_quote_line_item(db_session, project_quote, vendor):
    """Test creating a quote line item."""
    item = vendor_service.QuoteLineItems.create(
        db_session,
        QuoteLineItemCreate(
            quote_id=project_quote.id,
            item_type="fiber",
            description="Drop cable",
            cable_type="G.657A2",
            fiber_count=12,
            quantity=Decimal("50.000"),
            unit_price=Decimal("2.50"),
        ),
        vendor_id=str(vendor.id),
    )
    assert item.quote_id == project_quote.id
    assert item.item_type == "fiber"
    assert item.amount == Decimal("125.00")  # 50 * 2.50


def test_create_quote_line_item_wrong_vendor(db_session, project_quote, vendor2):
    """Test creating line item by wrong vendor raises 403."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.QuoteLineItems.create(
            db_session,
            QuoteLineItemCreate(
                quote_id=project_quote.id,
                item_type="labor",
                quantity=Decimal("1.000"),
                unit_price=Decimal("100.00"),
            ),
            vendor_id=str(vendor2.id),
        )
    assert exc_info.value.status_code == 403


def test_create_quote_line_item_updates_quote_total(db_session, project_quote, vendor):
    """Test that creating line items updates quote total."""
    vendor_service.QuoteLineItems.create(
        db_session,
        QuoteLineItemCreate(
            quote_id=project_quote.id,
            item_type="material",
            quantity=Decimal("10.000"),
            unit_price=Decimal("25.00"),
        ),
        vendor_id=str(vendor.id),
    )
    db_session.refresh(project_quote)
    assert project_quote.subtotal == Decimal("250.00")
    assert project_quote.total == Decimal("250.00")


def test_list_quote_line_items(db_session, quote_with_line_items):
    """Test listing quote line items."""
    items = vendor_service.QuoteLineItems.list(
        db_session,
        quote_id=str(quote_with_line_items.id),
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(items) == 2


def test_list_quote_line_items_all(db_session, quote_with_line_items):
    """Test listing all quote line items without filter."""
    items = vendor_service.QuoteLineItems.list(
        db_session,
        quote_id=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=100,
        offset=0,
    )
    assert len(items) >= 2


# =============================================================================
# Proposed Route Revisions Tests (Skipped due to PostGIS)
# =============================================================================


def test_proposed_route_revisions_list(db_session, project_quote):
    """Test listing proposed route revisions."""
    revisions = vendor_service.ProposedRouteRevisions.list(
        db_session,
        quote_id=str(project_quote.id),
        status=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert isinstance(revisions, list)


def test_proposed_route_revisions_list_invalid_status(db_session):
    """Test listing revisions with invalid status raises 400."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.ProposedRouteRevisions.list(
            db_session,
            quote_id=None,
            status="invalid_status",
            order_by="created_at",
            order_dir="asc",
            limit=10,
            offset=0,
        )
    assert exc_info.value.status_code == 400


def test_ensure_route_revision_not_found(db_session):
    """Test ensuring non-existent route revision raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service._ensure_route_revision(db_session, str(uuid.uuid4()))
    assert exc_info.value.status_code == 404
    assert "Route revision not found" in exc_info.value.detail


# =============================================================================
# As-Built Routes Tests (Skipped due to PostGIS)
# =============================================================================


def test_as_built_routes_list(db_session, installation_project):
    """Test listing as-built routes."""
    routes = vendor_service.AsBuiltRoutes.list(
        db_session,
        project_id=str(installation_project.id),
        status=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert isinstance(routes, list)


def test_as_built_routes_list_invalid_status(db_session):
    """Test listing as-built routes with invalid status raises 400."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.AsBuiltRoutes.list(
            db_session,
            project_id=None,
            status="invalid",
            order_by="created_at",
            order_dir="asc",
            limit=10,
            offset=0,
        )
    assert exc_info.value.status_code == 400


def test_ensure_as_built_not_found(db_session):
    """Test ensuring non-existent as-built raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service._ensure_as_built(db_session, str(uuid.uuid4()))
    assert exc_info.value.status_code == 404
    assert "As-built route not found" in exc_info.value.detail


# =============================================================================
# Installation Project Notes Tests
# =============================================================================


def test_create_installation_project_note(db_session, installation_project, person):
    """Test creating an installation project note."""
    note = vendor_service.InstallationProjectNotes.create(
        db_session,
        InstallationProjectNoteCreate(
            project_id=installation_project.id,
            author_person_id=person.id,
            body="Initial site survey completed",
            is_internal=False,
        ),
    )
    assert note.project_id == installation_project.id
    assert note.body == "Initial site survey completed"
    assert note.is_internal is False


def test_create_installation_project_note_internal(db_session, installation_project):
    """Test creating an internal project note."""
    note = vendor_service.InstallationProjectNotes.create(
        db_session,
        InstallationProjectNoteCreate(
            project_id=installation_project.id,
            body="Internal note: customer difficult to reach",
            is_internal=True,
        ),
    )
    assert note.is_internal is True


def test_create_installation_project_note_invalid_project(db_session):
    """Test creating note with invalid project raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.InstallationProjectNotes.create(
            db_session,
            InstallationProjectNoteCreate(
                project_id=uuid.uuid4(),
                body="Note body",
            ),
        )
    assert exc_info.value.status_code == 404


def test_create_installation_project_note_invalid_person(db_session, installation_project):
    """Test creating note with invalid author raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.InstallationProjectNotes.create(
            db_session,
            InstallationProjectNoteCreate(
                project_id=installation_project.id,
                author_person_id=uuid.uuid4(),
                body="Note body",
            ),
        )
    assert exc_info.value.status_code == 404


def test_list_installation_project_notes(db_session, installation_project, person):
    """Test listing installation project notes."""
    # Create some notes
    vendor_service.InstallationProjectNotes.create(
        db_session,
        InstallationProjectNoteCreate(
            project_id=installation_project.id,
            body="Note 1",
        ),
    )
    vendor_service.InstallationProjectNotes.create(
        db_session,
        InstallationProjectNoteCreate(
            project_id=installation_project.id,
            body="Note 2",
            is_internal=True,
        ),
    )

    notes = vendor_service.InstallationProjectNotes.list(
        db_session,
        project_id=str(installation_project.id),
        is_internal=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(notes) >= 2


def test_list_installation_project_notes_internal_only(db_session, installation_project):
    """Test listing only internal notes."""
    vendor_service.InstallationProjectNotes.create(
        db_session,
        InstallationProjectNoteCreate(
            project_id=installation_project.id,
            body="Internal note",
            is_internal=True,
        ),
    )

    notes = vendor_service.InstallationProjectNotes.list(
        db_session,
        project_id=str(installation_project.id),
        is_internal=True,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert all(n.is_internal is True for n in notes)


def test_list_installation_project_notes_external_only(db_session, installation_project):
    """Test listing only external notes."""
    vendor_service.InstallationProjectNotes.create(
        db_session,
        InstallationProjectNoteCreate(
            project_id=installation_project.id,
            body="External note",
            is_internal=False,
        ),
    )

    notes = vendor_service.InstallationProjectNotes.list(
        db_session,
        project_id=str(installation_project.id),
        is_internal=False,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert all(n.is_internal is False for n in notes)


# =============================================================================
# Helper Functions Tests
# =============================================================================


def test_ensure_buildout_project_not_found(db_session):
    """Test ensuring non-existent buildout project raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service._ensure_buildout_project(db_session, str(uuid.uuid4()))
    assert exc_info.value.status_code == 404
    assert "Buildout project not found" in exc_info.value.detail


def test_ensure_account_not_found(db_session):
    """Test ensuring non-existent account raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service._ensure_account(db_session, str(uuid.uuid4()))
    assert exc_info.value.status_code == 404
    assert "Subscriber account not found" in exc_info.value.detail


def test_ensure_address_not_found(db_session):
    """Test ensuring non-existent address raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service._ensure_address(db_session, str(uuid.uuid4()))
    assert exc_info.value.status_code == 404
    assert "Address not found" in exc_info.value.detail


def test_check_approval_required_below_threshold(db_session, project_quote):
    """Test approval not required when below threshold."""
    with patch.object(vendor_service.settings_spec, "resolve_value", return_value="5000"):
        project_quote.total = Decimal("1000.00")
        result = vendor_service.check_approval_required(db_session, project_quote)
    assert result is False


def test_check_approval_required_above_threshold(db_session, project_quote):
    """Test approval required when above threshold."""
    with patch.object(vendor_service.settings_spec, "resolve_value", return_value="5000"):
        project_quote.total = Decimal("10000.00")
        result = vendor_service.check_approval_required(db_session, project_quote)
    assert result is True


def test_check_approval_required_no_threshold(db_session, project_quote):
    """Test default threshold when setting not found."""
    with patch.object(vendor_service.settings_spec, "resolve_value", return_value=None):
        project_quote.total = Decimal("6000.00")
        result = vendor_service.check_approval_required(db_session, project_quote)
    assert result is True  # Default threshold is 5000


# =============================================================================
# Additional Coverage Tests
# =============================================================================


def test_create_installation_project_with_account(db_session, project, subscriber_account):
    """Test creating installation project with account."""
    ip = vendor_service.InstallationProjects.create(
        db_session,
        InstallationProjectCreate(
            project_id=project.id,
            account_id=subscriber_account.id,
        ),
    )
    assert ip.account_id == subscriber_account.id


def test_create_installation_project_invalid_account(db_session, project):
    """Test creating installation project with invalid account raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.InstallationProjects.create(
            db_session,
            InstallationProjectCreate(
                project_id=project.id,
                account_id=uuid.uuid4(),
            ),
        )
    assert exc_info.value.status_code == 404
    assert "Subscriber account not found" in exc_info.value.detail


def test_list_installation_projects_by_account(db_session, project, subscriber_account):
    """Test listing installation projects by account_id."""
    ip = vendor_service.InstallationProjects.create(
        db_session,
        InstallationProjectCreate(
            project_id=project.id,
            account_id=subscriber_account.id,
        ),
    )

    projects = vendor_service.InstallationProjects.list(
        db_session,
        status=None,
        vendor_id=None,
        account_id=str(subscriber_account.id),
        project_id=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert any(p.id == ip.id for p in projects)


def test_list_installation_projects_by_project(db_session, project):
    """Test listing installation projects by base project_id."""
    ip = vendor_service.InstallationProjects.create(
        db_session,
        InstallationProjectCreate(project_id=project.id),
    )

    projects = vendor_service.InstallationProjects.list(
        db_session,
        status=None,
        vendor_id=None,
        account_id=None,
        project_id=str(project.id),
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert any(p.project_id == project.id for p in projects)


def test_list_installation_projects_inactive(db_session, project):
    """Test listing inactive installation projects."""
    ip = vendor_service.InstallationProjects.create(
        db_session,
        InstallationProjectCreate(
            project_id=project.id,
            is_active=False,
        ),
    )

    projects = vendor_service.InstallationProjects.list(
        db_session,
        status=None,
        vendor_id=None,
        account_id=None,
        project_id=None,
        is_active=False,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert any(p.id == ip.id for p in projects)


def test_update_installation_project_with_account(db_session, installation_project, subscriber_account):
    """Test updating installation project with account."""
    updated = vendor_service.InstallationProjects.update(
        db_session,
        str(installation_project.id),
        InstallationProjectUpdate(account_id=subscriber_account.id),
    )
    assert updated.account_id == subscriber_account.id


def test_update_installation_project_invalid_account(db_session, installation_project):
    """Test updating installation project with invalid account raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.InstallationProjects.update(
            db_session,
            str(installation_project.id),
            InstallationProjectUpdate(account_id=uuid.uuid4()),
        )
    assert exc_info.value.status_code == 404
    assert "Subscriber account not found" in exc_info.value.detail


def test_update_installation_project_with_vendor(db_session, installation_project, vendor2):
    """Test updating installation project with different vendor."""
    updated = vendor_service.InstallationProjects.update(
        db_session,
        str(installation_project.id),
        InstallationProjectUpdate(assigned_vendor_id=vendor2.id),
    )
    assert updated.assigned_vendor_id == vendor2.id


def test_update_installation_project_invalid_vendor(db_session, installation_project):
    """Test updating installation project with invalid vendor raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.InstallationProjects.update(
            db_session,
            str(installation_project.id),
            InstallationProjectUpdate(assigned_vendor_id=uuid.uuid4()),
        )
    assert exc_info.value.status_code == 404
    assert "Vendor not found" in exc_info.value.detail


def test_list_project_quotes_by_project(db_session, project_quote, installation_project):
    """Test listing project quotes filtered by project_id."""
    quotes = vendor_service.ProjectQuotes.list(
        db_session,
        project_id=str(installation_project.id),
        vendor_id=None,
        status=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert any(q.id == project_quote.id for q in quotes)


def test_list_project_quotes_by_vendor(db_session, project_quote, vendor):
    """Test listing project quotes filtered by vendor_id."""
    quotes = vendor_service.ProjectQuotes.list(
        db_session,
        project_id=None,
        vendor_id=str(vendor.id),
        status=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert any(q.vendor_id == vendor.id for q in quotes)


def test_list_project_quotes_inactive(db_session, installation_project, vendor, person):
    """Test listing inactive project quotes."""
    def mock_resolve_value(db, domain, key):
        if key == "default_currency":
            return "USD"
        if key == "vendor_quote_validity_days":
            return "30"
        return None

    with patch.object(
        vendor_service.settings_spec, "resolve_value", side_effect=mock_resolve_value
    ):
        quote = vendor_service.ProjectQuotes.create(
            db_session,
            ProjectQuoteCreate(project_id=installation_project.id),
            vendor_id=str(vendor.id),
            created_by_person_id=str(person.id),
        )

    # Mark as inactive directly
    quote.is_active = False
    db_session.commit()

    quotes = vendor_service.ProjectQuotes.list(
        db_session,
        project_id=None,
        vendor_id=None,
        status=None,
        is_active=False,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert any(q.id == quote.id for q in quotes)


def test_list_project_quotes_order_by_total(db_session, project_quote):
    """Test listing project quotes ordered by total."""
    quotes = vendor_service.ProjectQuotes.list(
        db_session,
        project_id=None,
        vendor_id=None,
        status=None,
        is_active=None,
        order_by="total",
        order_dir="desc",
        limit=10,
        offset=0,
    )
    assert isinstance(quotes, list)


def test_list_quote_line_items_inactive(db_session, project_quote, vendor):
    """Test listing inactive quote line items."""
    item = vendor_service.QuoteLineItems.create(
        db_session,
        QuoteLineItemCreate(
            quote_id=project_quote.id,
            item_type="material",
            quantity=Decimal("10.000"),
            unit_price=Decimal("25.00"),
            is_active=False,
        ),
        vendor_id=str(vendor.id),
    )

    items = vendor_service.QuoteLineItems.list(
        db_session,
        quote_id=None,
        is_active=False,
        order_by="created_at",
        order_dir="asc",
        limit=100,
        offset=0,
    )
    assert any(i.id == item.id for i in items)


def test_create_quote_line_item_no_vendor(db_session, project_quote):
    """Test creating quote line item without vendor_id validation."""
    item = vendor_service.QuoteLineItems.create(
        db_session,
        QuoteLineItemCreate(
            quote_id=project_quote.id,
            item_type="equipment",
            quantity=Decimal("1.000"),
            unit_price=Decimal("500.00"),
        ),
        vendor_id=None,
    )
    assert item.amount == Decimal("500.00")


def test_list_proposed_route_revisions_by_status(db_session, project_quote):
    """Test listing proposed route revisions by status."""
    revisions = vendor_service.ProposedRouteRevisions.list(
        db_session,
        quote_id=None,
        status="draft",
        order_by="revision_number",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert isinstance(revisions, list)


def test_list_as_built_routes_by_status(db_session, installation_project):
    """Test listing as-built routes by status."""
    routes = vendor_service.AsBuiltRoutes.list(
        db_session,
        project_id=None,
        status="submitted",
        order_by="created_at",
        order_dir="desc",
        limit=10,
        offset=0,
    )
    assert isinstance(routes, list)


def test_submit_project_quote_for_bidding_project(db_session, project, vendor, person):
    """Test submitting quote for a bidding project updates project status."""
    # Create a fresh project for bidding to avoid timezone issues
    from app.schemas.projects import ProjectCreate
    from app.services import projects as projects_service

    base_project = projects_service.projects.create(
        db_session,
        ProjectCreate(name="Quote Submit Bidding Test"),
    )
    ip = vendor_service.InstallationProjects.create(
        db_session,
        InstallationProjectCreate(project_id=base_project.id),
    )

    # Open for bidding
    with patch.object(vendor_service.settings_spec, "resolve_value", return_value="7"):
        ip = vendor_service.InstallationProjects.open_for_bidding(
            db_session, str(ip.id), bid_days=30
        )

    def mock_resolve_value(db, domain, key):
        if key == "default_currency":
            return "USD"
        if key == "vendor_quote_validity_days":
            return "30"
        return None

    # Use naive datetime for comparison with SQLite stored datetime
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)

    with patch.object(vendor_service, "_now", return_value=now_naive):
        with patch.object(
            vendor_service.settings_spec, "resolve_value", side_effect=mock_resolve_value
        ):
            quote = vendor_service.ProjectQuotes.create(
                db_session,
                ProjectQuoteCreate(project_id=ip.id),
                vendor_id=str(vendor.id),
                created_by_person_id=str(person.id),
            )

    # Add line item
    vendor_service.QuoteLineItems.create(
        db_session,
        QuoteLineItemCreate(
            quote_id=quote.id,
            item_type="labor",
            quantity=Decimal("1.000"),
            unit_price=Decimal("100.00"),
        ),
        vendor_id=str(vendor.id),
    )

    submitted = vendor_service.ProjectQuotes.submit(
        db_session, str(quote.id), str(vendor.id)
    )

    assert submitted.status == ProjectQuoteStatus.submitted
    assert submitted.project.status == InstallationProjectStatus.quoted


def test_approve_project_quote_invalid_reviewer(db_session, quote_with_line_items, vendor):
    """Test approving quote with invalid reviewer raises 404."""
    vendor_service.ProjectQuotes.submit(
        db_session, str(quote_with_line_items.id), str(vendor.id)
    )

    with patch.object(vendor_service.settings_spec, "resolve_value", return_value="10000"):
        with pytest.raises(HTTPException) as exc_info:
            vendor_service.ProjectQuotes.approve(
                db_session,
                str(quote_with_line_items.id),
                reviewer_person_id=str(uuid.uuid4()),
                review_notes=None,
                override=False,
            )
    assert exc_info.value.status_code == 404


def test_submit_quote_from_revision_requested(db_session, project_quote, vendor):
    """Test submitting quote from revision_requested state."""
    # Set quote to revision_requested
    project_quote.status = ProjectQuoteStatus.revision_requested
    db_session.commit()

    submitted = vendor_service.ProjectQuotes.submit(
        db_session, str(project_quote.id), str(vendor.id)
    )
    assert submitted.status == ProjectQuoteStatus.submitted


def test_create_installation_project_with_address(db_session, project, subscriber):
    """Test creating installation project with address."""
    from app.models.subscriber import Address

    address = Address(
        subscriber_id=subscriber.id,
        address_line1="123 Main St",
        city="Test City",
        postal_code="12345",
    )
    db_session.add(address)
    db_session.commit()
    db_session.refresh(address)

    ip = vendor_service.InstallationProjects.create(
        db_session,
        InstallationProjectCreate(
            project_id=project.id,
            address_id=address.id,
        ),
    )
    assert ip.address_id == address.id


def test_create_installation_project_invalid_address(db_session, project):
    """Test creating installation project with invalid address raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.InstallationProjects.create(
            db_session,
            InstallationProjectCreate(
                project_id=project.id,
                address_id=uuid.uuid4(),
            ),
        )
    assert exc_info.value.status_code == 404
    assert "Address not found" in exc_info.value.detail


def test_update_installation_project_with_address(db_session, installation_project, subscriber):
    """Test updating installation project with address."""
    from app.models.subscriber import Address

    address = Address(
        subscriber_id=subscriber.id,
        address_line1="456 Update St",
        city="Update City",
        postal_code="67890",
    )
    db_session.add(address)
    db_session.commit()
    db_session.refresh(address)

    updated = vendor_service.InstallationProjects.update(
        db_session,
        str(installation_project.id),
        InstallationProjectUpdate(address_id=address.id),
    )
    assert updated.address_id == address.id


def test_update_installation_project_invalid_address(db_session, installation_project):
    """Test updating installation project with invalid address raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.InstallationProjects.update(
            db_session,
            str(installation_project.id),
            InstallationProjectUpdate(address_id=uuid.uuid4()),
        )
    assert exc_info.value.status_code == 404
    assert "Address not found" in exc_info.value.detail


def test_create_installation_project_with_buildout_project(db_session, project):
    """Test creating installation project with buildout project."""
    from app.models.qualification import BuildoutProject

    buildout = BuildoutProject(
        notes="Test Buildout Project",
    )
    db_session.add(buildout)
    db_session.commit()
    db_session.refresh(buildout)

    ip = vendor_service.InstallationProjects.create(
        db_session,
        InstallationProjectCreate(
            project_id=project.id,
            buildout_project_id=buildout.id,
        ),
    )
    assert ip.buildout_project_id == buildout.id


def test_create_installation_project_invalid_buildout(db_session, project):
    """Test creating installation project with invalid buildout raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.InstallationProjects.create(
            db_session,
            InstallationProjectCreate(
                project_id=project.id,
                buildout_project_id=uuid.uuid4(),
            ),
        )
    assert exc_info.value.status_code == 404
    assert "Buildout project not found" in exc_info.value.detail


def test_update_installation_project_with_buildout(db_session, installation_project):
    """Test updating installation project with buildout project."""
    from app.models.qualification import BuildoutProject

    buildout = BuildoutProject(
        notes="Update Buildout Project",
    )
    db_session.add(buildout)
    db_session.commit()
    db_session.refresh(buildout)

    updated = vendor_service.InstallationProjects.update(
        db_session,
        str(installation_project.id),
        InstallationProjectUpdate(buildout_project_id=buildout.id),
    )
    assert updated.buildout_project_id == buildout.id


def test_update_installation_project_invalid_buildout(db_session, installation_project):
    """Test updating installation project with invalid buildout raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        vendor_service.InstallationProjects.update(
            db_session,
            str(installation_project.id),
            InstallationProjectUpdate(buildout_project_id=uuid.uuid4()),
        )
    assert exc_info.value.status_code == 404
    assert "Buildout project not found" in exc_info.value.detail
