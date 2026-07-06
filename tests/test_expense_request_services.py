"""Tests for the expense request service and ERP sync mapping."""

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.expense_request import (
    ExpenseRequest,
    ExpenseRequestERPSyncStatus,
    ExpenseRequestItem,
    ExpenseRequestStatus,
)
from app.models.person import Person
from app.schemas.expense_request import ExpenseRequestCreate, ExpenseRequestItemCreate
from app.services.dotmac_erp.expense_request_sync import (
    DotMacERPExpenseRequestSync,
    ExpenseRequestSyncResult,
)
from app.services.expense_requests import expense_requests


def _create_payload(person, ticket=None, work_order=None, **overrides):
    data = {
        "requested_by_person_id": person.id,
        "purpose": "Site visit logistics",
        "items": [
            ExpenseRequestItemCreate(
                category_code="TRANSPORT",
                description="Fuel to site",
                amount=Decimal("7500.00"),
            )
        ],
    }
    if ticket is not None:
        data["ticket_id"] = ticket.id
    if work_order is not None:
        data["work_order_id"] = work_order.id
    data.update(overrides)
    return ExpenseRequestCreate(**data)


class TestExpenseRequestCreate:
    def test_create_submits_and_enqueues_erp_sync(self, db_session, person, ticket):
        with patch("app.tasks.integrations.sync_expense_request_to_erp.delay") as delay_mock:
            er = expense_requests.create(db_session, _create_payload(person, ticket=ticket))

        assert er.status == ExpenseRequestStatus.submitted
        assert er.submitted_at is not None
        assert er.expense_date == datetime.now(UTC).date()
        assert er.erp_sync_status == ExpenseRequestERPSyncStatus.pending
        assert len(er.items) == 1
        assert er.items[0].category_code == "TRANSPORT"
        assert er.items[0].expense_date == er.expense_date
        assert er.total_amount == Decimal("7500.00")
        delay_mock.assert_called_once_with(str(er.id))

    def test_create_inherits_context_from_work_order(self, db_session, person, work_order):
        with patch("app.tasks.integrations.sync_expense_request_to_erp.delay"):
            er = expense_requests.create(db_session, _create_payload(person, work_order=work_order))

        assert er.work_order_id == work_order.id
        assert er.ticket_id == work_order.ticket_id
        assert er.project_id == work_order.project_id

    def test_create_without_any_parent_is_allowed(self, db_session, person):
        with patch("app.tasks.integrations.sync_expense_request_to_erp.delay"):
            er = expense_requests.create(db_session, _create_payload(person))
        assert er.ticket_id is None
        assert er.project_id is None

    def test_create_requires_requester_email(self, db_session):
        no_email = Person(first_name="No", last_name="Email", email="   ")
        db_session.add(no_email)
        db_session.commit()

        with pytest.raises(HTTPException) as exc:
            expense_requests.create(db_session, _create_payload(no_email))
        assert exc.value.status_code == 400
        assert "email" in exc.value.detail.lower()

    def test_create_requires_at_least_one_item(self, person):
        with pytest.raises(ValueError):
            ExpenseRequestCreate(
                requested_by_person_id=person.id,
                purpose="Empty",
                items=[],
            )


class TestExpenseRequestList:
    def test_list_filters_by_status_and_requester(self, db_session, person, ticket):
        with patch("app.tasks.integrations.sync_expense_request_to_erp.delay"):
            er = expense_requests.create(db_session, _create_payload(person, ticket=ticket))

        rows = expense_requests.list(db_session, status="submitted", requested_by_person_id=str(person.id))
        assert any(row.id == er.id for row in rows)

        rows = expense_requests.list(db_session, status="paid", requested_by_person_id=str(person.id))
        assert not any(row.id == er.id for row in rows)

    def test_list_rejects_bad_date_range(self, db_session):
        with pytest.raises(HTTPException):
            expense_requests.list(
                db_session,
                created_from=date(2026, 7, 5),
                created_to=date(2026, 7, 1),
            )


class TestExpenseRequestCancel:
    def test_cancel_before_erp_sync(self, db_session, person, ticket):
        with patch("app.tasks.integrations.sync_expense_request_to_erp.delay"):
            er = expense_requests.create(db_session, _create_payload(person, ticket=ticket))
            er.erp_sync_status = ExpenseRequestERPSyncStatus.failed
            db_session.commit()

        canceled = expense_requests.cancel(db_session, str(er.id))
        assert canceled.status == ExpenseRequestStatus.canceled

    def test_cancel_blocked_after_claim_reached_erp(self, db_session, person, ticket):
        with patch("app.tasks.integrations.sync_expense_request_to_erp.delay"):
            er = expense_requests.create(db_session, _create_payload(person, ticket=ticket))
        er.erp_expense_claim_id = "claim-123"
        db_session.commit()

        with pytest.raises(HTTPException) as exc:
            expense_requests.cancel(db_session, str(er.id))
        assert exc.value.status_code == 400

    def test_cancel_blocked_in_terminal_status(self, db_session, person, ticket):
        with patch("app.tasks.integrations.sync_expense_request_to_erp.delay"):
            er = expense_requests.create(db_session, _create_payload(person, ticket=ticket))
        er.status = ExpenseRequestStatus.paid
        db_session.commit()

        with pytest.raises(HTTPException):
            expense_requests.cancel(db_session, str(er.id))


class TestExpenseRequestRetrySync:
    def test_retry_only_for_submitted(self, db_session, person, ticket):
        with patch("app.tasks.integrations.sync_expense_request_to_erp.delay"):
            er = expense_requests.create(db_session, _create_payload(person, ticket=ticket))
        er.status = ExpenseRequestStatus.rejected
        db_session.commit()

        with pytest.raises(HTTPException):
            expense_requests.retry_erp_sync(db_session, str(er.id))


@pytest.fixture()
def submitted_expense_request(db_session, person, ticket):
    er = ExpenseRequest(
        ticket_id=ticket.id,
        requested_by_person_id=person.id,
        purpose="Generator fuel for POP",
        status=ExpenseRequestStatus.submitted,
        expense_date=date(2026, 7, 6),
        submitted_at=datetime.now(UTC),
    )
    db_session.add(er)
    db_session.flush()
    db_session.add(
        ExpenseRequestItem(
            expense_request_id=er.id,
            category_code="FUEL",
            description="25L diesel",
            amount=Decimal("30000.00"),
        )
    )
    db_session.commit()
    db_session.refresh(er)
    return er


class TestExpenseRequestERPSync:
    def test_map_expense_request_payload(self, db_session, submitted_expense_request, person):
        sync = DotMacERPExpenseRequestSync(MagicMock(), db_session)
        payload = sync._map_expense_request(submitted_expense_request)

        assert payload["omni_id"] == str(submitted_expense_request.id)
        assert payload["purpose"] == "Generator fuel for POP"
        assert payload["claim_date"] == "2026-07-06"
        assert payload["requested_by_email"] == person.email
        assert payload["ticket_crm_id"] == str(submitted_expense_request.ticket_id)
        assert payload["items"] == [
            {
                "category_code": "FUEL",
                "description": "25L diesel",
                "claimed_amount": "30000.00",
                "expense_date": "2026-07-06",
            }
        ]

    def test_sync_pushes_and_records_claim(self, db_session, submitted_expense_request):
        client = MagicMock()
        client.push_expense_claim.return_value = {
            "claim_id": "erp-claim-1",
            "claim_number": "EXP-0001",
            "status": "pending_approval",
            "omni_id": str(submitted_expense_request.id),
        }
        sync = DotMacERPExpenseRequestSync(client, db_session)

        result = sync.sync_expense_request(submitted_expense_request)

        assert isinstance(result, ExpenseRequestSyncResult)
        assert result.success is True
        assert submitted_expense_request.erp_expense_claim_id == "erp-claim-1"
        assert submitted_expense_request.erp_claim_number == "EXP-0001"
        assert submitted_expense_request.erp_claim_status == "pending_approval"
        # pending_approval is not terminal — CRM row stays submitted
        assert submitted_expense_request.status == ExpenseRequestStatus.submitted
        idempotency_key = client.push_expense_claim.call_args.kwargs["idempotency_key"]
        assert idempotency_key == f"exp-{submitted_expense_request.id}-submit-v1"

    def test_sync_rejects_non_submitted(self, db_session, submitted_expense_request):
        submitted_expense_request.status = ExpenseRequestStatus.canceled
        db_session.commit()
        sync = DotMacERPExpenseRequestSync(MagicMock(), db_session)

        result = sync.sync_expense_request(submitted_expense_request)
        assert result.success is False
        assert result.error_type == "ValidationError"

    @pytest.mark.parametrize(
        ("erp_status", "expected_status"),
        [
            ("approved", ExpenseRequestStatus.approved),
            ("rejected", ExpenseRequestStatus.rejected),
            ("paid", ExpenseRequestStatus.paid),
            ("cancelled", ExpenseRequestStatus.canceled),
        ],
    )
    def test_refresh_maps_terminal_claim_statuses(
        self, db_session, submitted_expense_request, erp_status, expected_status
    ):
        submitted_expense_request.erp_expense_claim_id = "erp-claim-1"
        db_session.commit()
        client = MagicMock()
        client.get_expense_claim_status.return_value = {
            "claim_id": "erp-claim-1",
            "claim_number": "EXP-0001",
            "status": erp_status,
            "rejection_reason": "Over budget" if erp_status == "rejected" else None,
        }
        sync = DotMacERPExpenseRequestSync(client, db_session)

        result = sync.refresh_expense_request_status(submitted_expense_request)

        assert result.success is True
        assert submitted_expense_request.status == expected_status
        if erp_status == "rejected":
            assert submitted_expense_request.rejection_reason == "Over budget"
            assert submitted_expense_request.rejected_at is not None
        if erp_status == "paid":
            assert submitted_expense_request.paid_at is not None

    def test_refresh_handles_missing_claim(self, db_session, submitted_expense_request):
        client = MagicMock()
        client.get_expense_claim_status.return_value = None
        sync = DotMacERPExpenseRequestSync(client, db_session)

        result = sync.refresh_expense_request_status(submitted_expense_request)
        assert result.success is False
        assert result.error_type == "NotFound"
