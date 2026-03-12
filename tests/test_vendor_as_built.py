from decimal import Decimal

from app.schemas.vendor import (
    AsBuiltLineItemInput,
    AsBuiltRouteCreate,
    InstallationProjectCreate,
    ProjectQuoteCreate,
    QuoteLineItemCreate,
    VendorCreate,
)
from app.services import vendor as vendor_service


def test_as_built_create_persists_revised_line_items(db_session, project, person, monkeypatch):
    monkeypatch.setattr(vendor_service, "_geojson_to_geom", lambda _geojson: None)

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
            description="Approved installation labor",
            quantity=Decimal("1.000"),
            unit_price=Decimal("1000.00"),
        ),
        vendor_id=str(vendor.id),
    )
    vendor_service.project_quotes.submit(db_session, str(quote.id), vendor_id=str(vendor.id))

    as_built = vendor_service.as_built_routes.create(
        db_session,
        AsBuiltRouteCreate(
            project_id=installation_project.id,
            geojson={"type": "LineString", "coordinates": [[3.0, 6.0], [3.001, 6.001]]},
            actual_length_meters=157.25,
            line_items=[
                AsBuiltLineItemInput(
                    item_type="labor",
                    description="Additional trench restoration",
                    quantity=Decimal("2.000"),
                    unit_price=Decimal("1500.00"),
                    notes="Added after inspection",
                )
            ],
        ),
        vendor_id=str(vendor.id),
        submitted_by_person_id=str(person.id),
    )

    assert len(as_built.line_items) == 1
    assert as_built.line_items[0].description == "Additional trench restoration"
    assert as_built.line_items[0].amount == Decimal("3000.00")


def test_as_built_create_allows_line_items_without_route(db_session, project, person):
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
            description="Approved installation labor",
            quantity=Decimal("1.000"),
            unit_price=Decimal("1000.00"),
        ),
        vendor_id=str(vendor.id),
    )
    vendor_service.project_quotes.submit(db_session, str(quote.id), vendor_id=str(vendor.id))

    as_built = vendor_service.as_built_routes.create(
        db_session,
        AsBuiltRouteCreate(
            project_id=installation_project.id,
            line_items=[
                AsBuiltLineItemInput(
                    item_type="material",
                    description="Extra splice enclosure",
                    quantity=Decimal("1.000"),
                    unit_price=Decimal("250.00"),
                )
            ],
        ),
        vendor_id=str(vendor.id),
        submitted_by_person_id=str(person.id),
    )

    assert as_built.route_geom is None
    assert as_built.actual_length_meters is None
    assert len(as_built.line_items) == 1
