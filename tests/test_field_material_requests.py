"""Tests for technician-scoped field material requests."""

import uuid

import pytest
from fastapi import HTTPException

from app.models.inventory import InventoryItem
from app.models.person import Person
from app.schemas.field import FieldMaterialRequestCreate
from app.schemas.material_request import MaterialRequestItemCreate
from app.schemas.workforce import WorkOrderUpdate
from app.services.field.material_requests import field_material_requests
from app.services.workforce import work_orders


@pytest.fixture()
def assigned_job(db_session, work_order, person):
    return work_orders.update(db_session, str(work_order.id), WorkOrderUpdate(assigned_to_person_id=person.id))


@pytest.fixture()
def inventory_item(db_session):
    item = InventoryItem(name="Drop cable", sku="DC-150")
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


def test_field_create_material_request_from_assigned_job(db_session, assigned_job, person, inventory_item):
    request = field_material_requests.create(
        db_session,
        str(person.id),
        FieldMaterialRequestCreate(
            work_order_id=assigned_job.id,
            priority="high",
            items=[MaterialRequestItemCreate(item_id=inventory_item.id, quantity=2)],
        ),
    )

    assert request.work_order_id == assigned_job.id
    assert request.ticket_id == assigned_job.ticket_id
    assert request.project_id == assigned_job.project_id
    assert request.requested_by_person_id == person.id
    assert request.items[0].quantity == 2


def test_field_material_requests_are_scoped(db_session, assigned_job, person, inventory_item):
    request = field_material_requests.create(
        db_session,
        str(person.id),
        FieldMaterialRequestCreate(
            work_order_id=assigned_job.id,
            items=[MaterialRequestItemCreate(item_id=inventory_item.id, quantity=1)],
        ),
    )

    stranger = Person(first_name="Other", last_name="Tech", email=f"other-{uuid.uuid4().hex}@example.com")
    db_session.add(stranger)
    db_session.commit()

    assert field_material_requests.get_mine(db_session, str(person.id), str(request.id)).id == request.id
    with pytest.raises(HTTPException) as exc:
        field_material_requests.get_mine(db_session, str(stranger.id), str(request.id))
    assert exc.value.status_code == 404


def test_field_create_rejects_unassigned_job(db_session, assigned_job, inventory_item):
    stranger = Person(first_name="Other", last_name="Tech", email=f"other-{uuid.uuid4().hex}@example.com")
    db_session.add(stranger)
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        field_material_requests.create(
            db_session,
            str(stranger.id),
            FieldMaterialRequestCreate(
                work_order_id=assigned_job.id,
                items=[MaterialRequestItemCreate(item_id=inventory_item.id, quantity=1)],
            ),
        )
    assert exc.value.status_code == 404
