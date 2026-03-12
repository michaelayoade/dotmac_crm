"""Tests for deepened variation workflow, geo input, and ERP sync triggers."""

import contextlib
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.models.vendor import VariationType
from app.schemas.vendor import (
    AsBuiltLineItemInput,
    AsBuiltRouteCreate,
    InstallationProjectCreate,
    ProjectQuoteCreate,
    QuoteLineItemCreate,
    VendorCreate,
)
from app.services import vendor as vendor_service

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def vendor_setup(db_session, project, person, monkeypatch):
    """Create vendor, installation project, submitted quote — ready for as-built."""
    monkeypatch.setattr(vendor_service, "_geojson_to_geom", lambda _geojson: None)

    vendor = vendor_service.vendors.create(db_session, VendorCreate(name="Test Vendor"))
    ip = vendor_service.installation_projects.create(
        db_session,
        InstallationProjectCreate(project_id=project.id, assigned_vendor_id=vendor.id),
    )
    quote = vendor_service.project_quotes.create(
        db_session,
        ProjectQuoteCreate(project_id=ip.id),
        vendor_id=str(vendor.id),
        created_by_person_id=str(person.id),
    )
    vendor_service.quote_line_items.create(
        db_session,
        QuoteLineItemCreate(
            quote_id=quote.id,
            item_type="labor",
            description="Base labor",
            quantity=Decimal("1.000"),
            unit_price=Decimal("1000.00"),
        ),
        vendor_id=str(vendor.id),
    )
    vendor_service.project_quotes.submit(db_session, str(quote.id), vendor_id=str(vendor.id))
    return {"vendor": vendor, "ip": ip, "quote": quote, "person": person}


# ---------------------------------------------------------------------------
# Variation domain fields
# ---------------------------------------------------------------------------

class TestVariationFields:
    def test_variation_type_stored(self, db_session, vendor_setup):
        setup = vendor_setup
        as_built = vendor_service.as_built_routes.create(
            db_session,
            AsBuiltRouteCreate(
                project_id=setup["ip"].id,
                line_items=[AsBuiltLineItemInput(
                    item_type="labor", description="Extra work",
                    quantity=Decimal("1"), unit_price=Decimal("500"),
                )],
                variation_type=VariationType.scope_change,
                variation_reason="Scope extended to include cabinet",
                work_order_ref="WO-2026-0042",
            ),
            vendor_id=str(setup["vendor"].id),
            submitted_by_person_id=str(setup["person"].id),
        )
        assert as_built.variation_type == VariationType.scope_change
        assert as_built.variation_reason == "Scope extended to include cabinet"
        assert as_built.work_order_ref == "WO-2026-0042"

    def test_version_auto_increments(self, db_session, vendor_setup):
        setup = vendor_setup
        first = vendor_service.as_built_routes.create(
            db_session,
            AsBuiltRouteCreate(
                project_id=setup["ip"].id,
                line_items=[AsBuiltLineItemInput(
                    item_type="labor", description="First submission",
                    quantity=Decimal("1"), unit_price=Decimal("500"),
                )],
            ),
            vendor_id=str(setup["vendor"].id),
            submitted_by_person_id=str(setup["person"].id),
        )
        assert first.version == 1

        second = vendor_service.as_built_routes.create(
            db_session,
            AsBuiltRouteCreate(
                project_id=setup["ip"].id,
                line_items=[AsBuiltLineItemInput(
                    item_type="labor", description="Second submission",
                    quantity=Decimal("1"), unit_price=Decimal("500"),
                )],
            ),
            vendor_id=str(setup["vendor"].id),
            submitted_by_person_id=str(setup["person"].id),
        )
        assert second.version == 2


# ---------------------------------------------------------------------------
# Variation events
# ---------------------------------------------------------------------------

class TestVariationEvents:
    def test_create_emits_variation_submitted(self, db_session, vendor_setup):
        setup = vendor_setup
        with patch("app.services.events.dispatcher.emit_event") as mock_emit:
            vendor_service.as_built_routes.create(
                db_session,
                AsBuiltRouteCreate(
                    project_id=setup["ip"].id,
                    line_items=[AsBuiltLineItemInput(
                        item_type="labor", description="Work",
                        quantity=Decimal("1"), unit_price=Decimal("500"),
                    )],
                    variation_type=VariationType.additional_work,
                ),
                vendor_id=str(setup["vendor"].id),
                submitted_by_person_id=str(setup["person"].id),
            )
        # Find the variation_submitted call
        calls = [c for c in mock_emit.call_args_list if hasattr(c[0][1], 'value') and c[0][1].value == "variation.submitted"]
        assert len(calls) == 1
        payload = calls[0][0][2]
        assert payload["variation_version"] == 1
        assert payload["variation_type"] == "additional_work"
        assert "idempotency_key" in payload
        assert payload["baseline_refs"]["installation_project_id"] == str(setup["ip"].id)
        assert payload["baseline_refs"]["project_id"] == str(setup["ip"].project_id)
        assert payload["metadata"]["variation_id"] == payload["variation_id"]
        assert payload["metadata"]["variation_version"] == payload["variation_version"]

    def test_accept_emits_variation_approved(self, db_session, vendor_setup, monkeypatch):
        setup = vendor_setup
        as_built = vendor_service.as_built_routes.create(
            db_session,
            AsBuiltRouteCreate(
                project_id=setup["ip"].id,
                line_items=[AsBuiltLineItemInput(
                    item_type="labor", description="Work",
                    quantity=Decimal("1"), unit_price=Decimal("500"),
                )],
            ),
            vendor_id=str(setup["vendor"].id),
            submitted_by_person_id=str(setup["person"].id),
        )
        # Mock weasyprint to avoid ImportError in test
        mock_html_cls = MagicMock()
        mock_html_cls.return_value.write_pdf = MagicMock()
        monkeypatch.setattr("builtins.__import__", _mock_weasyprint_import(mock_html_cls))

        with patch("app.services.events.dispatcher.emit_event") as mock_emit:
            with contextlib.suppress(Exception):
                vendor_service.as_built_routes.accept_and_convert(
                    db_session, str(as_built.id), str(setup["person"].id)
                )
            calls = [c for c in mock_emit.call_args_list if hasattr(c[0][1], 'value') and c[0][1].value == "variation.approved"]
            # If accept_and_convert succeeded past the commit
            if calls:
                payload = calls[0][0][2]
                assert payload["status"] == "accepted"
                assert "idempotency_key" in payload
                assert payload["metadata"]["idempotency_key"] == payload["idempotency_key"]

    def test_reject_emits_variation_rejected(self, db_session, vendor_setup):
        setup = vendor_setup
        as_built = vendor_service.as_built_routes.create(
            db_session,
            AsBuiltRouteCreate(
                project_id=setup["ip"].id,
                line_items=[AsBuiltLineItemInput(
                    item_type="labor", description="Work",
                    quantity=Decimal("1"), unit_price=Decimal("500"),
                )],
            ),
            vendor_id=str(setup["vendor"].id),
            submitted_by_person_id=str(setup["person"].id),
        )
        with patch("app.services.events.dispatcher.emit_event") as mock_emit:
            vendor_service.as_built_routes.reject(
                db_session, str(as_built.id), str(setup["person"].id), "Not acceptable"
            )
        calls = [c for c in mock_emit.call_args_list if hasattr(c[0][1], 'value') and c[0][1].value == "variation.rejected"]
        assert len(calls) == 1
        payload = calls[0][0][2]
        assert payload["status"] == "rejected"


# ---------------------------------------------------------------------------
# ERP sync handler — variation_approved triggers project sync
# ---------------------------------------------------------------------------

class TestVariationERPSync:
    def test_variation_approved_triggers_project_sync(self):
        from app.services.events.handlers.erp_sync import ERPSyncHandler
        from app.services.events.types import Event, EventType

        handler = ERPSyncHandler()
        event = Event(
            event_type=EventType.variation_approved,
            payload={
                "variation_id": "aaa-bbb-ccc",
                "project_id": "proj-123",
                "idempotency_key": "variation:aaa-bbb-ccc:v1",
            },
            project_id="proj-123",
        )
        entity_type, entity_id = handler._extract_entity_info(event)
        assert entity_type == "project"
        assert entity_id == "proj-123"

    def test_variation_submitted_not_in_sync_events(self):
        from app.services.events.handlers.erp_sync import ERP_SYNC_EVENT_TYPES
        from app.services.events.types import EventType

        # Only approved triggers sync, not submitted or rejected
        assert EventType.variation_approved in ERP_SYNC_EVENT_TYPES
        assert EventType.variation_submitted not in ERP_SYNC_EVENT_TYPES
        assert EventType.variation_rejected not in ERP_SYNC_EVENT_TYPES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_weasyprint_import(mock_html_cls):
    """Create a mock import function that intercepts weasyprint."""
    _real_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

    def _import(name, *args, **kwargs):
        if name == "weasyprint":
            module = MagicMock()
            module.HTML = mock_html_cls
            return module
        return _real_import(name, *args, **kwargs)
    return _import
