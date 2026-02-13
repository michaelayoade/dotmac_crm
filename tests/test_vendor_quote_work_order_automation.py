from __future__ import annotations

import uuid

from app.models.workforce import WorkOrder
from app.schemas.vendor import InstallationProjectCreate, ProjectQuoteCreate, VendorCreate
from app.services import vendor as vendor_service
from app.services.automation_actions import execute_actions
from app.services.events.types import Event, EventType


def test_vendor_quote_submit_emits_event(db_session, project):
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
        created_by_person_id=None,
    )

    captured: dict = {}

    def _emit_event_spy(db, event_type, payload, **kwargs):
        captured["event_type"] = event_type
        captured["payload"] = payload
        captured["kwargs"] = kwargs
        return Event(event_type=event_type, payload=payload, **kwargs)

    import app.services.events.dispatcher as dispatcher

    original_emit_event = dispatcher.emit_event
    dispatcher.emit_event = _emit_event_spy
    try:
        vendor_service.project_quotes.submit(db_session, str(quote.id), vendor_id=str(vendor.id))
    finally:
        dispatcher.emit_event = original_emit_event

    assert captured["event_type"] == EventType.vendor_quote_submitted
    assert captured["payload"]["quote_id"] == str(quote.id)
    assert captured["payload"]["installation_project_id"] == str(installation_project.id)
    assert captured["payload"]["project_id"] == str(project.id)
    assert captured["payload"]["vendor_id"] == str(vendor.id)
    assert captured["payload"]["project_name"] == project.name
    assert captured["payload"]["project_code"] == project.code
    assert captured["payload"]["vendor_name"] == vendor.name
    assert captured["kwargs"]["project_id"] == project.id


def test_vendor_quote_approve_emits_event(db_session, project, person):
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
        created_by_person_id=None,
    )
    vendor_service.project_quotes.submit(db_session, str(quote.id), vendor_id=str(vendor.id))

    captured: dict = {}

    def _emit_event_spy(db, event_type, payload, **kwargs):
        captured["event_type"] = event_type
        captured["payload"] = payload
        captured["kwargs"] = kwargs
        return Event(event_type=event_type, payload=payload, **kwargs)

    import app.services.events.dispatcher as dispatcher

    original_emit_event = dispatcher.emit_event
    dispatcher.emit_event = _emit_event_spy
    try:
        vendor_service.project_quotes.approve(
            db_session,
            quote_id=str(quote.id),
            reviewer_person_id=str(person.id),
            review_notes=None,
            override=True,
        )
    finally:
        dispatcher.emit_event = original_emit_event

    assert captured["event_type"] == EventType.vendor_quote_approved
    assert captured["payload"]["quote_id"] == str(quote.id)
    assert captured["payload"]["installation_project_id"] == str(installation_project.id)
    assert captured["payload"]["project_id"] == str(project.id)
    assert captured["payload"]["vendor_id"] == str(vendor.id)
    assert captured["kwargs"]["project_id"] == project.id


def test_create_work_order_upsert_updates_existing(db_session, project, person):
    action = {
        "action_type": "create_work_order",
        "params": {
            "title_template": "Vendor Quote WO - {project_code} - {vendor_name}",
            "upsert_existing": True,
            "match_title_exact": True,
            "source_name": "vendor_quote_work_order_automation",
        },
    }
    payload = {
        "quote_id": str(uuid.uuid4()),
        "installation_project_id": str(uuid.uuid4()),
        "project_code": "PRJ-1001",
        "vendor_name": "Acme Vendor",
    }

    event = Event(
        event_type=EventType.vendor_quote_submitted,
        payload=payload,
        project_id=project.id,
    )

    execute_actions(db_session, [action], event)
    first = db_session.query(WorkOrder).filter(WorkOrder.project_id == project.id).one()
    first_id = first.id

    action["params"]["assigned_technician_id"] = str(person.id)
    execute_actions(db_session, [action], event)

    work_orders = db_session.query(WorkOrder).filter(WorkOrder.project_id == project.id).all()
    assert len(work_orders) == 1
    assert work_orders[0].id == first_id
    assert work_orders[0].title == "Vendor Quote WO - PRJ-1001 - Acme Vendor"
    assert work_orders[0].assigned_to_person_id == person.id
    assert work_orders[0].metadata_["automation_source"] == "vendor_quote_work_order_automation"
    assert work_orders[0].metadata_["source_event_type"] == EventType.vendor_quote_submitted.value
    assert work_orders[0].metadata_["source_quote_id"] == payload["quote_id"]


def test_create_work_order_title_template_fallback_when_project_code_missing(db_session, project):
    action = {
        "action_type": "create_work_order",
        "params": {
            "title_template": "Vendor Quote WO - {project_code} - {vendor_name}",
            "upsert_existing": True,
            "match_title_exact": False,
            "source_name": "vendor_quote_work_order_automation",
        },
    }
    payload = {
        "quote_id": str(uuid.uuid4()),
        "installation_project_id": str(uuid.uuid4()),
        "vendor_name": "Miracle Racheal David",
    }
    event = Event(
        event_type=EventType.vendor_quote_submitted,
        payload=payload,
        project_id=project.id,
    )

    execute_actions(db_session, [action], event)
    wo = db_session.query(WorkOrder).filter(WorkOrder.project_id == project.id).one()
    assert "{project_code}" not in wo.title
    assert "Miracle Racheal David" in wo.title
