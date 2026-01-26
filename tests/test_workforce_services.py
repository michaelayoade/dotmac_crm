"""Tests for workforce service."""

from app.models.workforce import WorkOrderStatus, WorkOrderPriority
from app.schemas.workforce import (
    WorkOrderCreate, WorkOrderUpdate,
    WorkOrderAssignmentCreate, WorkOrderAssignmentUpdate,
    WorkOrderNoteCreate,
)
from app.services import workforce as workforce_service


def test_create_work_order(db_session, subscriber_account):
    """Test creating a work order."""
    order = workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(
            title="Install Fiber",
            account_id=subscriber_account.id,
            priority=WorkOrderPriority.high,
        ),
    )
    assert order.title == "Install Fiber"
    assert order.account_id == subscriber_account.id
    assert order.priority == WorkOrderPriority.high


def test_create_work_order_with_ticket(db_session, subscriber_account, ticket):
    """Test creating a work order linked to a ticket."""
    order = workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(
            title="Repair Service",
            account_id=subscriber_account.id,
            ticket_id=ticket.id,
        ),
    )
    assert order.ticket_id == ticket.id


def test_create_work_order_with_project(db_session, subscriber_account, project):
    """Test creating a work order linked to a project."""
    order = workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(
            title="Project Task",
            account_id=subscriber_account.id,
            project_id=project.id,
        ),
    )
    assert order.project_id == project.id


def test_list_work_orders_by_status(db_session, subscriber_account):
    """Test listing work orders by status."""
    workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(
            title="Draft Order",
            account_id=subscriber_account.id,
            status=WorkOrderStatus.draft,
        ),
    )
    workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(
            title="Completed Order",
            account_id=subscriber_account.id,
            status=WorkOrderStatus.completed,
        ),
    )

    drafts = workforce_service.work_orders.list(
        db_session,
        account_id=None,
        subscription_id=None,
        service_order_id=None,
        ticket_id=None,
        project_id=None,
        assigned_to_person_id=None,
        status=WorkOrderStatus.draft.value,
        priority=None,
        work_type=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert all(o.status == WorkOrderStatus.draft for o in drafts)


def test_list_work_orders_by_account(db_session, subscriber_account):
    """Test listing work orders by account."""
    workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(title="Order 1", account_id=subscriber_account.id),
    )
    workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(title="Order 2", account_id=subscriber_account.id),
    )

    orders = workforce_service.work_orders.list(
        db_session,
        account_id=str(subscriber_account.id),
        subscription_id=None,
        service_order_id=None,
        ticket_id=None,
        project_id=None,
        assigned_to_person_id=None,
        status=None,
        priority=None,
        work_type=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(orders) >= 2
    assert all(o.account_id == subscriber_account.id for o in orders)


def test_update_work_order(db_session, subscriber_account):
    """Test updating a work order."""
    order = workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(
            title="Original Title",
            account_id=subscriber_account.id,
            status=WorkOrderStatus.draft,
        ),
    )
    updated = workforce_service.work_orders.update(
        db_session,
        str(order.id),
        WorkOrderUpdate(
            title="Updated Title",
            status=WorkOrderStatus.in_progress,
        ),
    )
    assert updated.title == "Updated Title"
    assert updated.status == WorkOrderStatus.in_progress


def test_delete_work_order(db_session, subscriber_account):
    """Test deleting a work order."""
    order = workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(title="To Delete", account_id=subscriber_account.id),
    )
    workforce_service.work_orders.delete(db_session, str(order.id))
    db_session.refresh(order)
    assert order.is_active is False


def test_create_work_order_assignment(db_session, subscriber_account, person):
    """Test creating a work order assignment."""
    order = workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(title="Assigned Order", account_id=subscriber_account.id),
    )
    assignment = workforce_service.work_order_assignments.create(
        db_session,
        WorkOrderAssignmentCreate(
            work_order_id=order.id,
            person_id=person.id,
        ),
    )
    assert assignment.work_order_id == order.id
    assert assignment.person_id == person.id


def test_list_assignments_by_work_order(db_session, subscriber_account, person):
    """Test listing assignments by work order."""
    order = workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(title="Multi Assignment", account_id=subscriber_account.id),
    )
    workforce_service.work_order_assignments.create(
        db_session,
        WorkOrderAssignmentCreate(work_order_id=order.id, person_id=person.id),
    )

    assignments = workforce_service.work_order_assignments.list(
        db_session,
        work_order_id=str(order.id),
        person_id=None,
        order_by="assigned_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(assignments) >= 1
    assert all(a.work_order_id == order.id for a in assignments)


def test_update_assignment(db_session, subscriber_account, person):
    """Test updating a work order assignment."""
    order = workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(title="Update Assignment", account_id=subscriber_account.id),
    )
    assignment = workforce_service.work_order_assignments.create(
        db_session,
        WorkOrderAssignmentCreate(work_order_id=order.id, person_id=person.id),
    )
    updated = workforce_service.work_order_assignments.update(
        db_session,
        str(assignment.id),
        WorkOrderAssignmentUpdate(role="Lead Technician", is_primary=True),
    )
    assert updated.role == "Lead Technician"
    assert updated.is_primary is True


def test_delete_assignment(db_session, subscriber_account, person):
    """Test deleting a work order assignment (hard delete)."""
    import pytest
    from fastapi import HTTPException

    order = workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(title="Delete Assignment", account_id=subscriber_account.id),
    )
    assignment = workforce_service.work_order_assignments.create(
        db_session,
        WorkOrderAssignmentCreate(work_order_id=order.id, person_id=person.id),
    )
    assignment_id = str(assignment.id)
    workforce_service.work_order_assignments.delete(db_session, assignment_id)
    # Assignment uses hard delete, should raise 404
    with pytest.raises(HTTPException) as exc_info:
        workforce_service.work_order_assignments.get(db_session, assignment_id)
    assert exc_info.value.status_code == 404


def test_create_work_order_note(db_session, subscriber_account, person):
    """Test creating a work order note."""
    order = workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(title="Note Order", account_id=subscriber_account.id),
    )
    note = workforce_service.work_order_notes.create(
        db_session,
        WorkOrderNoteCreate(
            work_order_id=order.id,
            author_person_id=person.id,
            body="Work started on site",
        ),
    )
    assert note.work_order_id == order.id
    assert note.body == "Work started on site"


def test_list_notes_by_work_order(db_session, subscriber_account, person):
    """Test listing notes by work order."""
    order = workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(title="Notes Order", account_id=subscriber_account.id),
    )
    workforce_service.work_order_notes.create(
        db_session,
        WorkOrderNoteCreate(
            work_order_id=order.id,
            author_person_id=person.id,
            body="Note 1",
        ),
    )
    workforce_service.work_order_notes.create(
        db_session,
        WorkOrderNoteCreate(
            work_order_id=order.id,
            author_person_id=person.id,
            body="Note 2",
        ),
    )

    notes = workforce_service.work_order_notes.list(
        db_session,
        work_order_id=str(order.id),
        is_internal=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(notes) >= 2
    assert all(n.work_order_id == order.id for n in notes)


def test_delete_work_order_note(db_session, subscriber_account, person):
    """Test deleting a work order note (hard delete)."""
    import pytest
    from fastapi import HTTPException

    order = workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(title="Delete Note", account_id=subscriber_account.id),
    )
    note = workforce_service.work_order_notes.create(
        db_session,
        WorkOrderNoteCreate(
            work_order_id=order.id,
            author_person_id=person.id,
            body="To be deleted",
        ),
    )
    note_id = str(note.id)
    workforce_service.work_order_notes.delete(db_session, note_id)
    # Note uses hard delete, should raise 404
    with pytest.raises(HTTPException) as exc_info:
        workforce_service.work_order_notes.get(db_session, note_id)
    assert exc_info.value.status_code == 404


def test_get_work_order(db_session, subscriber_account):
    """Test getting a work order by ID."""
    order = workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(
            title="Get Test",
            account_id=subscriber_account.id,
            description="Test description",
        ),
    )
    fetched = workforce_service.work_orders.get(db_session, str(order.id))
    assert fetched is not None
    assert fetched.id == order.id
    assert fetched.title == "Get Test"
