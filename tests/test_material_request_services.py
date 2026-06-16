from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.inventory import InventoryItem, InventoryLocation
from app.models.material_request import (
    MaterialRequest,
    MaterialRequestERPSyncStatus,
    MaterialRequestPriority,
    MaterialRequestStatus,
)
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


@pytest.fixture()
def inventory_location(db_session):
    location = InventoryLocation(name="Main Warehouse", code="WH-MAIN")
    db_session.add(location)
    db_session.commit()
    db_session.refresh(location)
    return location


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
        assert mr.status == MaterialRequestStatus.submitted
        assert mr.submitted_at is not None
        assert mr.ticket_id == ticket.id
        assert mr.requested_by_person_id == person.id
        assert mr.erp_sync_status is None
        assert mr.erp_sync_error is None
        assert mr.erp_synced_at is None
        assert mr.erp_sync_attempts == 0

    def test_create_requires_ticket_or_project(self, db_session, person):
        payload = MaterialRequestCreate(requested_by_person_id=person.id)

        with pytest.raises(HTTPException) as exc:
            material_requests.create(db_session, payload)

        assert exc.value.status_code == 400
        assert "ticket or" in str(exc.value.detail).lower()

    def test_create_rejects_same_source_and_destination(
        self,
        db_session,
        person,
        ticket,
        inventory_location,
    ):
        payload = MaterialRequestCreate(
            ticket_id=ticket.id,
            requested_by_person_id=person.id,
            source_location_id=inventory_location.id,
            destination_location_id=inventory_location.id,
        )

        with pytest.raises(HTTPException) as exc:
            material_requests.create(db_session, payload)

        assert exc.value.status_code == 400
        assert "cannot be the same" in str(exc.value.detail)

    def test_erp_sync_status_values(self):
        assert [status.value for status in MaterialRequestERPSyncStatus] == [
            "pending",
            "synced",
            "failed",
            "retrying",
            "not_configured",
        ]

    def test_create_assigns_generated_number(self, db_session, person, ticket):
        with patch("app.services.material_requests.generate_number", return_value="MR-0001"):
            mr = _make_mr(db_session, person, ticket)
        assert mr.number == "MR-0001"

    def test_create_without_number_when_disabled(self, db_session, person, ticket):
        with patch("app.services.material_requests.generate_number", return_value=None):
            mr = _make_mr(db_session, person, ticket)
        assert mr.number is None

    def test_create_with_items(self, db_session, person, ticket, inventory_item):
        mr = _make_mr(
            db_session,
            person,
            ticket,
            items=[MaterialRequestItemCreate(item_id=inventory_item.id, quantity=3)],
        )
        assert len(mr.items) == 1
        assert mr.items[0].quantity == 3

    def test_create_with_item_serial_numbers(self, db_session, person, ticket, inventory_item):
        mr = _make_mr(
            db_session,
            person,
            ticket,
            items=[
                MaterialRequestItemCreate(
                    item_id=inventory_item.id,
                    quantity=2,
                    serial_numbers=["ONT-001", "ONT-002"],
                )
            ],
        )
        assert mr.items[0].serial_numbers == ["ONT-001", "ONT-002"]

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

    def test_list_by_date_range(self, db_session, person, ticket):
        older = _make_mr(db_session, person, ticket)
        newer = _make_mr(db_session, person, ticket)

        now = datetime.now(UTC)
        older.created_at = now - timedelta(days=10)
        newer.created_at = now - timedelta(days=1)
        db_session.commit()

        items = material_requests.list(
            db_session,
            created_from=(now - timedelta(days=2)).date(),
            created_to=now.date(),
        )

        item_ids = {item.id for item in items}
        assert newer.id in item_ids
        assert older.id not in item_ids

    def test_list_by_status_and_date_range(self, db_session, person, ticket):
        in_range = _make_mr(db_session, person, ticket)
        out_of_status = _make_mr(db_session, person, ticket)

        now = datetime.now(UTC)
        in_range.created_at = now - timedelta(days=1)
        out_of_status.created_at = now - timedelta(days=1)
        db_session.commit()

        material_requests.cancel(db_session, str(in_range.id))

        items = material_requests.list(
            db_session,
            status="canceled",
            created_from=(now - timedelta(days=2)).date(),
            created_to=now.date(),
        )

        item_ids = {item.id for item in items}
        assert in_range.id in item_ids
        assert out_of_status.id not in item_ids

    def test_list_by_erp_material_status(self, db_session, person, ticket):
        pending_stock = _make_mr(db_session, person, ticket)
        synced = _make_mr(db_session, person, ticket)
        pending_stock.erp_material_status = "pending_stock"
        synced.erp_sync_status = MaterialRequestERPSyncStatus.synced
        db_session.commit()

        items = material_requests.list(db_session, erp_status="pending stock")

        item_ids = {item.id for item in items}
        assert pending_stock.id in item_ids
        assert synced.id not in item_ids

    def test_list_by_erp_sync_status(self, db_session, person, ticket):
        failed = _make_mr(db_session, person, ticket)
        pending_stock = _make_mr(db_session, person, ticket)
        failed.erp_sync_status = MaterialRequestERPSyncStatus.failed
        pending_stock.erp_material_status = "pending_stock"
        db_session.commit()

        items = material_requests.list(db_session, erp_status="failed")

        item_ids = {item.id for item in items}
        assert failed.id in item_ids
        assert pending_stock.id not in item_ids

    def test_list_by_ticket(self, db_session, person, ticket):
        _make_mr(db_session, person, ticket)
        items = material_requests.list(db_session, ticket_id=str(ticket.id))
        assert all(i.ticket_id == ticket.id for i in items)

    def test_update(self, db_session, person, ticket):
        mr = _make_mr(db_session, person, ticket)
        updated = material_requests.update(
            db_session,
            str(mr.id),
            MaterialRequestUpdate(priority=MaterialRequestPriority.urgent),
        )
        assert updated.priority == MaterialRequestPriority.urgent


class TestMaterialRequestStatusTransitions:
    def test_submit(self, db_session, person, ticket):
        mr = MaterialRequest(ticket_id=ticket.id, requested_by_person_id=person.id)
        db_session.add(mr)
        db_session.commit()
        db_session.refresh(mr)
        submitted = material_requests.submit(db_session, str(mr.id))
        assert submitted.status == MaterialRequestStatus.submitted
        assert submitted.submitted_at is not None

    def test_submit_non_draft_fails(self, db_session, person, ticket):
        mr = _make_mr(db_session, person, ticket)
        with pytest.raises(HTTPException) as exc:
            material_requests.submit(db_session, str(mr.id))
        assert exc.value.status_code == 400

    def test_approve(self, db_session, person, ticket, inventory_location):
        mr = _make_mr(db_session, person, ticket)
        with patch("app.tasks.integrations.sync_material_request_to_erp.delay"):
            approved = material_requests.approve(
                db_session,
                str(mr.id),
                str(person.id),
                source_location_id=str(inventory_location.id),
            )
        assert approved.status == MaterialRequestStatus.issued
        assert approved.approved_at is not None
        assert approved.approved_by_person_id == person.id
        assert approved.source_location_id == inventory_location.id
        assert approved.erp_sync_status == MaterialRequestERPSyncStatus.pending
        assert approved.erp_sync_error is None

    def test_approve_sets_collected_by(self, db_session, person, ticket, inventory_location):
        mr = _make_mr(db_session, person, ticket)
        with patch("app.tasks.integrations.sync_material_request_to_erp.delay"):
            approved = material_requests.approve(
                db_session,
                str(mr.id),
                str(person.id),
                source_location_id=str(inventory_location.id),
                collected_by_person_id=str(person.id),
            )
        assert approved.collected_by_person_id == person.id

    def test_approve_enqueues_erp_sync(self, db_session, person, ticket, inventory_location):
        mr = _make_mr(db_session, person, ticket)
        with patch("app.tasks.integrations.sync_material_request_to_erp.delay") as delay_mock:
            approved = material_requests.approve(
                db_session,
                str(mr.id),
                str(person.id),
                source_location_id=str(inventory_location.id),
            )

        delay_mock.assert_called_once_with(str(mr.id))
        assert approved.erp_sync_status == MaterialRequestERPSyncStatus.pending

    def test_approve_saves_selected_serial_numbers(
        self, db_session, person, ticket, inventory_item, inventory_location
    ):
        mr = _make_mr(
            db_session,
            person,
            ticket,
            items=[MaterialRequestItemCreate(item_id=inventory_item.id, quantity=2)],
        )
        line = mr.items[0]

        with patch("app.tasks.integrations.sync_material_request_to_erp.delay"):
            approved = material_requests.approve(
                db_session,
                str(mr.id),
                str(person.id),
                source_location_id=str(inventory_location.id),
                serial_numbers_by_item={str(line.id): ["ONT-001", "ONT-002"]},
            )

        assert approved.items[0].serial_numbers == ["ONT-001", "ONT-002"]

    def test_approve_rejects_wrong_serial_count(self, db_session, person, ticket, inventory_item, inventory_location):
        mr = _make_mr(
            db_session,
            person,
            ticket,
            items=[MaterialRequestItemCreate(item_id=inventory_item.id, quantity=2)],
        )
        line = mr.items[0]

        with pytest.raises(HTTPException) as exc:
            material_requests.approve(
                db_session,
                str(mr.id),
                str(person.id),
                source_location_id=str(inventory_location.id),
                serial_numbers_by_item={str(line.id): ["ONT-001"]},
            )

        assert exc.value.status_code == 400
        assert "exactly 2 serial" in str(exc.value.detail)

    def test_approve_requires_serials_when_erp_tracks_item(
        self,
        db_session,
        person,
        ticket,
        inventory_item,
        inventory_location,
        monkeypatch,
    ):
        mr = _make_mr(
            db_session,
            person,
            ticket,
            items=[MaterialRequestItemCreate(item_id=inventory_item.id, quantity=1)],
        )
        mock_sync_service = MagicMock()
        mock_sync_service.client.get_inventory_items.return_value = [{"item_code": inventory_item.sku}]
        mock_sync_service.client.list_available_serials.return_value = {
            "track_serial_numbers": True,
            "serials": [{"serial_number": "ONT-001"}],
            "has_more": False,
        }

        monkeypatch.setattr(
            "app.services.dotmac_erp.material_request_sync.dotmac_erp_material_request_sync",
            lambda session: mock_sync_service,
        )

        with pytest.raises(HTTPException) as exc:
            material_requests.approve(
                db_session,
                str(mr.id),
                str(person.id),
                source_location_id=str(inventory_location.id),
            )

        assert exc.value.status_code == 400
        assert "exactly 1 serial" in str(exc.value.detail)

    def test_approve_marks_sync_failed_when_enqueue_fails(self, db_session, person, ticket, inventory_location):
        mr = _make_mr(db_session, person, ticket)
        with patch(
            "app.tasks.integrations.sync_material_request_to_erp.delay",
            side_effect=RuntimeError("queue unavailable"),
        ):
            approved = material_requests.approve(
                db_session,
                str(mr.id),
                str(person.id),
                source_location_id=str(inventory_location.id),
            )

        assert approved.status == MaterialRequestStatus.issued
        assert approved.erp_sync_status == MaterialRequestERPSyncStatus.failed
        assert "queue unavailable" in (approved.erp_sync_error or "")

    def test_retry_erp_sync_marks_pending_and_enqueues(self, db_session, person, ticket, inventory_location):
        mr = _make_mr(db_session, person, ticket)
        with patch("app.tasks.integrations.sync_material_request_to_erp.delay"):
            approved = material_requests.approve(
                db_session,
                str(mr.id),
                str(person.id),
                source_location_id=str(inventory_location.id),
            )
        approved.erp_sync_status = MaterialRequestERPSyncStatus.failed
        approved.erp_sync_error = "previous failure"
        db_session.commit()

        with patch("app.tasks.integrations.sync_material_request_to_erp.delay") as delay_mock:
            retried = material_requests.retry_erp_sync(db_session, str(approved.id))

        delay_mock.assert_called_once_with(str(approved.id))
        assert retried.erp_sync_status == MaterialRequestERPSyncStatus.pending
        assert retried.erp_sync_error is None

    def test_retry_erp_sync_records_enqueue_failure(self, db_session, person, ticket, inventory_location):
        mr = _make_mr(db_session, person, ticket)
        with patch("app.tasks.integrations.sync_material_request_to_erp.delay"):
            approved = material_requests.approve(
                db_session,
                str(mr.id),
                str(person.id),
                source_location_id=str(inventory_location.id),
            )

        with patch(
            "app.tasks.integrations.sync_material_request_to_erp.delay",
            side_effect=RuntimeError("queue unavailable"),
        ):
            retried = material_requests.retry_erp_sync(db_session, str(approved.id))

        assert retried.erp_sync_status == MaterialRequestERPSyncStatus.failed
        assert "queue unavailable" in (retried.erp_sync_error or "")

    def test_approve_requires_source_warehouse(self, db_session, person, ticket):
        mr = _make_mr(db_session, person, ticket)
        with pytest.raises(HTTPException) as exc:
            material_requests.approve(db_session, str(mr.id), str(person.id))
        assert exc.value.status_code == 400
        assert "source warehouse" in str(exc.value.detail).lower()

    def test_approve_non_submitted_fails(self, db_session, person, ticket, inventory_location):
        mr = MaterialRequest(ticket_id=ticket.id, requested_by_person_id=person.id)
        db_session.add(mr)
        db_session.commit()
        db_session.refresh(mr)
        with pytest.raises(HTTPException) as exc:
            material_requests.approve(
                db_session,
                str(mr.id),
                str(person.id),
                source_location_id=str(inventory_location.id),
            )
        assert exc.value.status_code == 400

    def test_approve_blocks_when_erp_item_code_missing(self, db_session, person, ticket, inventory_location):
        mr = _make_mr(db_session, person, ticket)
        with (
            patch(
                "app.services.material_requests._validate_items_exist_in_erp",
                side_effect=HTTPException(status_code=400, detail="Item code not found in ERP"),
            ),
            pytest.raises(HTTPException) as exc,
        ):
            material_requests.approve(
                db_session,
                str(mr.id),
                str(person.id),
                source_location_id=str(inventory_location.id),
            )
        assert exc.value.status_code == 400

    def test_reject(self, db_session, person, ticket):
        mr = _make_mr(db_session, person, ticket)
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
            db_session,
            str(mr.id),
            MaterialRequestItemCreate(item_id=inventory_item.id, quantity=5),
        )
        assert item.material_request_id == mr.id
        assert item.quantity == 5

    def test_remove_item(self, db_session, person, ticket, inventory_item):
        mr = _make_mr(
            db_session,
            person,
            ticket,
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
                db_session,
                str(mr.id),
                MaterialRequestItemCreate(item_id=inventory_item.id, quantity=1),
            )
        assert exc.value.status_code == 400
