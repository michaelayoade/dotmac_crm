"""Tests for the CRM-local expense request service."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models.expense_request import (
    ExpenseRequestERPSyncStatus,
    ExpenseRequestStatus,
)
from app.models.person import Person
from app.schemas.expense_request import ExpenseRequestCreate, ExpenseRequestItemCreate
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
    def test_create_submits_locally_without_starting_external_sync(self, db_session, person, ticket):
        er = expense_requests.create(db_session, _create_payload(person, ticket=ticket))

        assert er.status == ExpenseRequestStatus.submitted
        assert er.submitted_at is not None
        assert er.expense_date == datetime.now(UTC).date()
        assert er.erp_sync_status is None
        assert len(er.items) == 1
        assert er.items[0].category_code == "TRANSPORT"
        assert er.items[0].expense_date == er.expense_date
        assert er.total_amount == Decimal("7500.00")

    def test_create_inherits_context_from_work_order(self, db_session, person, work_order):
        er = expense_requests.create(db_session, _create_payload(person, work_order=work_order))

        assert er.work_order_id == work_order.id
        assert er.ticket_id == work_order.ticket_id
        assert er.project_id == work_order.project_id

    def test_create_without_any_parent_is_allowed(self, db_session, person):
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
        er = expense_requests.create(db_session, _create_payload(person, ticket=ticket))
        er.erp_sync_status = ExpenseRequestERPSyncStatus.failed
        db_session.commit()

        canceled = expense_requests.cancel(db_session, str(er.id))
        assert canceled.status == ExpenseRequestStatus.canceled

    def test_cancel_blocked_after_claim_reached_erp(self, db_session, person, ticket):
        er = expense_requests.create(db_session, _create_payload(person, ticket=ticket))
        er.erp_expense_claim_id = "claim-123"
        db_session.commit()

        with pytest.raises(HTTPException) as exc:
            expense_requests.cancel(db_session, str(er.id))
        assert exc.value.status_code == 400

    def test_cancel_blocked_in_terminal_status(self, db_session, person, ticket):
        er = expense_requests.create(db_session, _create_payload(person, ticket=ticket))
        er.status = ExpenseRequestStatus.paid
        db_session.commit()

        with pytest.raises(HTTPException):
            expense_requests.cancel(db_session, str(er.id))
