"""Tests for technician-scoped field expense requests."""

import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.models.person import Person
from app.schemas.expense_request import ExpenseRequestItemCreate
from app.schemas.field import FieldExpenseRequestCreate
from app.schemas.workforce import WorkOrderUpdate
from app.services.field.expense_requests import field_expense_requests
from app.services.workforce import work_orders


@pytest.fixture()
def assigned_job(db_session, work_order, person):
    return work_orders.update(db_session, str(work_order.id), WorkOrderUpdate(assigned_to_person_id=person.id))


def _payload(**overrides):
    data = {
        "purpose": "Transport to customer site",
        "items": [
            ExpenseRequestItemCreate(
                category_code="TRANSPORT",
                description="Keke to site and back",
                amount=Decimal("2500.00"),
            )
        ],
    }
    data.update(overrides)
    return FieldExpenseRequestCreate(**data)


def test_field_create_expense_request_from_assigned_job(db_session, assigned_job, person):
    with patch("app.tasks.integrations.sync_expense_request_to_erp.delay"):
        er = field_expense_requests.create(db_session, str(person.id), _payload(work_order_id=assigned_job.id))

    assert er.work_order_id == assigned_job.id
    assert er.ticket_id == assigned_job.ticket_id
    assert er.project_id == assigned_job.project_id
    assert er.requested_by_person_id == person.id
    assert er.purpose == "Transport to customer site"
    assert er.items[0].amount == Decimal("2500.00")


def test_field_create_expense_request_without_job(db_session, person):
    with patch("app.tasks.integrations.sync_expense_request_to_erp.delay"):
        er = field_expense_requests.create(db_session, str(person.id), _payload())
    assert er.work_order_id is None


def test_field_create_rejects_unassigned_work_order(db_session, work_order, person):
    with pytest.raises(HTTPException) as exc:
        field_expense_requests.create(db_session, str(person.id), _payload(work_order_id=work_order.id))
    assert exc.value.status_code in (403, 404)


def test_field_expense_requests_are_personal(db_session, person, ticket):
    other = Person(first_name="Other", last_name="Tech", email=f"other-{uuid.uuid4().hex[:8]}@dotmac.test")
    db_session.add(other)
    db_session.commit()

    with patch("app.tasks.integrations.sync_expense_request_to_erp.delay"):
        er = field_expense_requests.create(db_session, str(person.id), _payload(ticket_id=ticket.id))

    mine = field_expense_requests.list_mine(db_session, str(person.id))
    assert any(row.id == er.id for row in mine)

    theirs = field_expense_requests.list_mine(db_session, str(other.id))
    assert not any(row.id == er.id for row in theirs)

    with pytest.raises(HTTPException) as exc:
        field_expense_requests.get_mine(db_session, str(other.id), str(er.id))
    assert exc.value.status_code == 404


def test_field_cancel_own_request(db_session, person, ticket):
    with patch("app.tasks.integrations.sync_expense_request_to_erp.delay"):
        er = field_expense_requests.create(db_session, str(person.id), _payload(ticket_id=ticket.id))
        er.erp_sync_status = None
        db_session.commit()

    canceled = field_expense_requests.cancel(db_session, str(person.id), str(er.id))
    assert canceled.status.value == "canceled"


def test_field_list_mine_filters_status(db_session, person, ticket):
    with patch("app.tasks.integrations.sync_expense_request_to_erp.delay"):
        er = field_expense_requests.create(db_session, str(person.id), _payload(ticket_id=ticket.id))

    submitted = field_expense_requests.list_mine(db_session, str(person.id), status="submitted")
    assert any(row.id == er.id for row in submitted)

    paid = field_expense_requests.list_mine(db_session, str(person.id), status="paid")
    assert not any(row.id == er.id for row in paid)

    with pytest.raises(HTTPException):
        field_expense_requests.list_mine(db_session, str(person.id), status="bogus")


def test_field_categories_empty_when_erp_not_configured(db_session, monkeypatch):
    def _raise(_session):
        raise ValueError("not configured")

    monkeypatch.setattr(
        "app.services.dotmac_erp.expense_request_sync.dotmac_erp_expense_request_sync",
        _raise,
    )
    assert field_expense_requests.list_categories(db_session) == []
