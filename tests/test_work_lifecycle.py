from app.models.work_lifecycle import (
    WorkEntityType,
    WorkLink,
    WorkLinkType,
    WorkOutcomeStatus,
    WorkOutcomeType,
)
from app.models.workforce import WorkOrder
from app.schemas.projects import ProjectTaskCreate, ProjectTaskUpdate
from app.schemas.workforce import WorkOrderCreate
from app.services import projects as projects_service
from app.services import workforce as workforce_service
from app.services.work_lifecycle import work_lifecycle


def test_work_order_create_records_ticket_origin(db_session, ticket):
    work_order = workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(title="Replace drop cable", ticket_id=ticket.id),
    )

    link = (
        db_session.query(WorkLink)
        .filter(WorkLink.source_type == WorkEntityType.ticket)
        .filter(WorkLink.source_id == ticket.id)
        .filter(WorkLink.target_type == WorkEntityType.work_order)
        .filter(WorkLink.target_id == work_order.id)
        .filter(WorkLink.link_type == WorkLinkType.originated)
        .one_or_none()
    )

    assert link is not None
    assert link.contract_name == "work_order.created_from_ticket"


def test_project_task_create_records_work_order_origin(db_session, project, work_order):
    task = projects_service.project_tasks.create(
        db_session,
        ProjectTaskCreate(
            project_id=project.id,
            title="Complete field splice",
            work_order_id=work_order.id,
        ),
    )

    link = (
        db_session.query(WorkLink)
        .filter(WorkLink.source_type == WorkEntityType.project_task)
        .filter(WorkLink.source_id == task.id)
        .filter(WorkLink.target_type == WorkEntityType.work_order)
        .filter(WorkLink.target_id == work_order.id)
        .filter(WorkLink.link_type == WorkLinkType.originated)
        .one_or_none()
    )

    assert link is not None
    assert link.contract_name == "project_task.linked_work_order"


def test_project_task_update_records_work_order_origin(db_session, project_task, work_order):
    task = projects_service.project_tasks.update(
        db_session,
        str(project_task.id),
        ProjectTaskUpdate(work_order_id=work_order.id),
    )

    link = (
        db_session.query(WorkLink)
        .filter(WorkLink.source_type == WorkEntityType.project_task)
        .filter(WorkLink.source_id == task.id)
        .filter(WorkLink.target_type == WorkEntityType.work_order)
        .filter(WorkLink.target_id == work_order.id)
        .filter(WorkLink.link_type == WorkLinkType.originated)
        .one_or_none()
    )

    assert link is not None
    assert link.contract_name == "project_task.linked_work_order"


def test_work_lifecycle_link_is_idempotent(db_session, ticket):
    work_order = WorkOrder(title="Survey pole route", ticket_id=ticket.id)
    db_session.add(work_order)
    db_session.flush()

    first = work_lifecycle.link_work_order_origin(
        db_session,
        work_order_id=work_order.id,
        origin_type="ticket",
        origin_id=ticket.id,
        contract_name="ticket.field_visit.created_work_order",
    )
    second = work_lifecycle.link_work_order_origin(
        db_session,
        work_order_id=work_order.id,
        origin_type="ticket",
        origin_id=ticket.id,
        contract_name="ticket.field_visit.created_work_order",
    )

    assert second.id == first.id
    assert db_session.query(WorkLink).count() == 1


def test_work_outcome_idempotency_key_reuses_existing_record(db_session, work_order):
    first = work_lifecycle.create_outcome(
        db_session,
        work_order_id=work_order.id,
        outcome_type=WorkOutcomeType.no_billing_change,
        status=WorkOutcomeStatus.succeeded,
        idempotency_key=f"work-order:{work_order.id}:no-billing-change",
        payload={"reason": "internal_job"},
    )
    second = work_lifecycle.create_outcome(
        db_session,
        work_order_id=work_order.id,
        outcome_type=WorkOutcomeType.no_billing_change,
        status=WorkOutcomeStatus.succeeded,
        idempotency_key=f"work-order:{work_order.id}:no-billing-change",
        payload={"reason": "retry"},
    )

    assert second.id == first.id
    assert first.outcome_type == WorkOutcomeType.no_billing_change
    assert first.status == WorkOutcomeStatus.succeeded
