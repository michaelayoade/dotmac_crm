import pytest
from fastapi import HTTPException

from app.models.inventory import InventoryItem
from app.models.material_request import MaterialRequestPriority, MaterialRequestStatus
from app.schemas.material_request import (
    MaterialRequestCreate,
    MaterialRequestItemCreate,
    MaterialRequestUpdate,
)
from app.services.material_requests import material_requests


@pytest.fixture()
def inventory_item(db_session):
    item = InventoryItem(name="Fiber Splice Closure", sku="FIB-SC-001")
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


def _make_mr(db, person, ticket, inventory_item=None, items=None):
    payload = MaterialRequestCreate(
        ticket_id=ticket.id,
        requested_by_person_id=person.id,
        items=items,
    )
    return material_requests.create(db, payload)


class TestMaterialRequestCRUD:
    def test_create(self, db_session, person, ticket):
        mr = _make_mr(db_session, person, ticket)
        assert mr.status == MaterialRequestStatus.draft
        assert mr.ticket_id == ticket.id
        assert mr.requested_by_person_id == person.id

    def test_create_with_items(self, db_session, person, ticket, inventory_item):
        mr = _make_mr(
            db_session, person, ticket,
            items=[MaterialRequestItemCreate(item_id=inventory_item.id, quantity=3)],
        )
        assert len(mr.items) == 1
        assert mr.items[0].quantity == 3

    def test_get(self, db_session, person, ticket):
        mr = _make_mr(db_session, person, ticket)
        fetched = material_requests.get(db_session, str(mr.id))
        assert fetched.id == mr.id

    def test_get_not_found(self, db_session):
        with pytest.raises(HTTPException) as exc:
            material_requests.get(db_session, "00000000-0000-0000-0000-000000000000")
        assert exc.value.status_code == 404

    def test_list(self, db_session, person, ticket):
        _make_mr(db_session, person, ticket)
        items = material_requests.list(db_session)
        assert len(items) >= 1

    def test_list_by_ticket(self, db_session, person, ticket):
        _make_mr(db_session, person, ticket)
        items = material_requests.list(db_session, ticket_id=str(ticket.id))
        assert all(i.ticket_id == ticket.id for i in items)

    def test_update(self, db_session, person, ticket):
        mr = _make_mr(db_session, person, ticket)
        updated = material_requests.update(
            db_session, str(mr.id),
            MaterialRequestUpdate(priority=MaterialRequestPriority.urgent),
        )
        assert updated.priority == MaterialRequestPriority.urgent


class TestMaterialRequestStatusTransitions:
    def test_submit(self, db_session, person, ticket):
        mr = _make_mr(db_session, person, ticket)
        submitted = material_requests.submit(db_session, str(mr.id))
        assert submitted.status == MaterialRequestStatus.submitted
        assert submitted.submitted_at is not None

    def test_submit_non_draft_fails(self, db_session, person, ticket):
        mr = _make_mr(db_session, person, ticket)
        material_requests.submit(db_session, str(mr.id))
        with pytest.raises(HTTPException) as exc:
            material_requests.submit(db_session, str(mr.id))
        assert exc.value.status_code == 400

    def test_approve(self, db_session, person, ticket):
        mr = _make_mr(db_session, person, ticket)
        material_requests.submit(db_session, str(mr.id))
        approved = material_requests.approve(db_session, str(mr.id), str(person.id))
        assert approved.status == MaterialRequestStatus.approved
        assert approved.approved_at is not None
        assert approved.approved_by_person_id == person.id

    def test_approve_non_submitted_fails(self, db_session, person, ticket):
        mr = _make_mr(db_session, person, ticket)
        with pytest.raises(HTTPException) as exc:
            material_requests.approve(db_session, str(mr.id), str(person.id))
        assert exc.value.status_code == 400

    def test_reject(self, db_session, person, ticket):
        mr = _make_mr(db_session, person, ticket)
        material_requests.submit(db_session, str(mr.id))
        rejected = material_requests.reject(db_session, str(mr.id), str(person.id), "Out of budget")
        assert rejected.status == MaterialRequestStatus.rejected
        assert rejected.rejected_at is not None
        assert "Out of budget" in (rejected.notes or "")

    def test_cancel(self, db_session, person, ticket):
        mr = _make_mr(db_session, person, ticket)
        canceled = material_requests.cancel(db_session, str(mr.id))
        assert canceled.status == MaterialRequestStatus.canceled

    def test_cancel_terminal_fails(self, db_session, person, ticket):
        mr = _make_mr(db_session, person, ticket)
        material_requests.cancel(db_session, str(mr.id))
        with pytest.raises(HTTPException) as exc:
            material_requests.cancel(db_session, str(mr.id))
        assert exc.value.status_code == 400


class TestMaterialRequestItems:
    def test_add_item(self, db_session, person, ticket, inventory_item):
        mr = _make_mr(db_session, person, ticket)
        item = material_requests.add_item(
            db_session, str(mr.id),
            MaterialRequestItemCreate(item_id=inventory_item.id, quantity=5),
        )
        assert item.material_request_id == mr.id
        assert item.quantity == 5

    def test_remove_item(self, db_session, person, ticket, inventory_item):
        mr = _make_mr(
            db_session, person, ticket,
            items=[MaterialRequestItemCreate(item_id=inventory_item.id, quantity=2)],
        )
        mr = material_requests.get(db_session, str(mr.id))
        assert len(mr.items) == 1
        material_requests.remove_item(db_session, str(mr.id), str(mr.items[0].id))
        mr = material_requests.get(db_session, str(mr.id))
        assert len(mr.items) == 0

    def test_cannot_add_item_to_terminal(self, db_session, person, ticket, inventory_item):
        mr = _make_mr(db_session, person, ticket)
        material_requests.cancel(db_session, str(mr.id))
        with pytest.raises(HTTPException) as exc:
            material_requests.add_item(
                db_session, str(mr.id),
                MaterialRequestItemCreate(item_id=inventory_item.id, quantity=1),
            )
        assert exc.value.status_code == 400
