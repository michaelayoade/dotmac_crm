"""Tests for field material consumption."""

import uuid

import pytest
from fastapi import HTTPException

from app.models.inventory import (
    InventoryItem,
    InventoryLocation,
    InventoryStock,
    MaterialStatus,
    Reservation,
    ReservationStatus,
    WorkOrderMaterial,
)
from app.models.material_request import MaterialRequest, MaterialRequestStatus
from app.schemas.workforce import WorkOrderUpdate
from app.services.field.materials import field_materials
from app.services.workforce import work_orders


@pytest.fixture()
def assigned_job(db_session, work_order, person):
    return work_orders.update(db_session, str(work_order.id), WorkOrderUpdate(assigned_to_person_id=person.id))


@pytest.fixture()
def stocked_material(db_session, assigned_job):
    item = InventoryItem(name="Drop cable", sku="DC-150")
    location = InventoryLocation(name="Main warehouse")
    db_session.add_all([item, location])
    db_session.flush()
    stock = InventoryStock(item_id=item.id, location_id=location.id, quantity_on_hand=10, reserved_quantity=5)
    db_session.add(stock)
    db_session.flush()
    reservation = Reservation(
        item_id=item.id,
        location_id=location.id,
        work_order_id=assigned_job.id,
        quantity=5,
        status=ReservationStatus.active,
    )
    db_session.add(reservation)
    db_session.flush()
    material = WorkOrderMaterial(
        work_order_id=assigned_job.id,
        item_id=item.id,
        reservation_id=reservation.id,
        quantity=5,
        status=MaterialStatus.reserved,
    )
    db_session.add(material)
    db_session.commit()
    db_session.refresh(material)
    return material, stock, reservation


def test_partial_consumption(db_session, assigned_job, person, stocked_material):
    material, _stock, reservation = stocked_material
    updated = field_materials.consume(
        db_session,
        str(person.id),
        str(assigned_job.id),
        [{"material_id": str(material.id), "consumed_quantity": 3, "leftover_note": "2 left in van"}],
    )
    assert updated[0].consumed_quantity == 3
    assert updated[0].status == MaterialStatus.reserved
    assert "2 left in van" in updated[0].notes
    # Partial consumption does not release the reservation or touch stock.
    db_session.refresh(reservation)
    assert reservation.status == ReservationStatus.active


def test_full_consumption_decrements_stock_and_fulfills_request(
    db_session, assigned_job, person, stocked_material
):
    material, stock, reservation = stocked_material
    request = MaterialRequest(
        work_order_id=assigned_job.id,
        project_id=assigned_job.project_id,
        requested_by_person_id=person.id,
        status=MaterialRequestStatus.issued,
    )
    db_session.add(request)
    db_session.commit()

    field_materials.consume(
        db_session,
        str(person.id),
        str(assigned_job.id),
        [{"material_id": str(material.id), "consumed_quantity": 5}],
    )

    db_session.refresh(material)
    db_session.refresh(stock)
    db_session.refresh(reservation)
    db_session.refresh(request)
    assert material.status == MaterialStatus.used
    assert reservation.status == ReservationStatus.consumed
    assert stock.quantity_on_hand == 5
    assert stock.reserved_quantity == 0
    assert request.status == MaterialRequestStatus.fulfilled
    assert request.fulfilled_at is not None


def test_over_consumption_rejected(db_session, assigned_job, person, stocked_material):
    material, _, _ = stocked_material
    with pytest.raises(HTTPException) as exc:
        field_materials.consume(
            db_session,
            str(person.id),
            str(assigned_job.id),
            [{"material_id": str(material.id), "consumed_quantity": 6}],
        )
    assert exc.value.status_code == 422


def test_material_from_other_job_404(db_session, assigned_job, person, stocked_material, project, ticket):
    from app.schemas.workforce import WorkOrderCreate

    other_job = work_orders.create(
        db_session,
        WorkOrderCreate(title="Other", project_id=project.id, ticket_id=ticket.id, assigned_to_person_id=person.id),
    )
    material, _, _ = stocked_material
    with pytest.raises(HTTPException) as exc:
        field_materials.consume(
            db_session,
            str(person.id),
            str(other_job.id),
            [{"material_id": str(material.id), "consumed_quantity": 1}],
        )
    assert exc.value.status_code == 404


def test_request_not_fulfilled_while_materials_remain(db_session, assigned_job, person, stocked_material):
    material, _, _ = stocked_material
    item2 = InventoryItem(name="ONT unit")
    db_session.add(item2)
    db_session.flush()
    db_session.add(WorkOrderMaterial(work_order_id=assigned_job.id, item_id=item2.id, quantity=1))
    request = MaterialRequest(
        work_order_id=assigned_job.id,
        project_id=assigned_job.project_id,
        requested_by_person_id=person.id,
        status=MaterialRequestStatus.issued,
    )
    db_session.add(request)
    db_session.commit()

    field_materials.consume(
        db_session,
        str(person.id),
        str(assigned_job.id),
        [{"material_id": str(material.id), "consumed_quantity": 5}],
    )
    db_session.refresh(request)
    assert request.status == MaterialRequestStatus.issued


def test_list_for_job_scoped(db_session, assigned_job, person, stocked_material):
    materials = field_materials.list_for_job(db_session, str(person.id), str(assigned_job.id))
    assert len(materials) == 1

    from app.models.person import Person

    stranger = Person(first_name="S", last_name="T", email=f"s-{uuid.uuid4().hex}@example.com")
    db_session.add(stranger)
    db_session.commit()
    with pytest.raises(HTTPException):
        field_materials.list_for_job(db_session, str(stranger.id), str(assigned_job.id))
