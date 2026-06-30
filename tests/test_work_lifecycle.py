from uuid import uuid4

from app.models.subscriber import Subscriber
from app.models.work_lifecycle import (
    WorkEntityType,
    WorkLink,
    WorkLinkType,
    WorkOutcome,
    WorkOutcomeStatus,
    WorkOutcomeType,
)
from app.models.workforce import WorkOrder, WorkOrderStatus, WorkOrderType
from app.queries.workforce import WorkOrderQuery
from app.schemas.projects import ProjectTaskCreate, ProjectTaskUpdate
from app.schemas.workforce import WorkOrderCreate, WorkOrderUpdate
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


def test_work_order_completion_records_selfcare_outcome(db_session, person, monkeypatch):
    subscriber = Subscriber(
        person_id=person.id,
        external_system="selfcare",
        external_id="sub-123",
        subscriber_number="SUB-123",
    )
    db_session.add(subscriber)
    db_session.flush()
    work_order = workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(
            title="Install subscriber service",
            work_type=WorkOrderType.install,
            subscriber_id=subscriber.id,
        ),
    )

    def _notify(_db, event_type, payload):
        assert event_type == "work_order.completed"
        assert payload["subscriber_id"] == "sub-123"
        return True

    monkeypatch.setattr("app.services.selfcare.notify_work_order_event", _notify)

    workforce_service.work_orders.update(
        db_session,
        str(work_order.id),
        WorkOrderUpdate(status=WorkOrderStatus.completed),
    )

    outcome = db_session.query(WorkOutcome).filter(WorkOutcome.work_order_id == work_order.id).one()
    assert outcome.outcome_type == WorkOutcomeType.activation_requested
    assert outcome.status == WorkOutcomeStatus.succeeded
    assert outcome.external_system == "selfcare"
    assert outcome.external_reference == "sub-123"


def test_work_order_query_reads_ticket_origin_link(db_session, ticket):
    work_order = WorkOrder(title="Linked by WorkLink only")
    db_session.add(work_order)
    db_session.flush()
    work_lifecycle.link_work_order_origin(
        db_session,
        work_order_id=work_order.id,
        origin_type="ticket",
        origin_id=ticket.id,
        contract_name="ticket.created_work_order",
    )
    db_session.commit()

    results = WorkOrderQuery(db_session).by_ticket(ticket.id).all()

    assert work_order.id in {item.id for item in results}


def test_dangling_links_reports_missing_source(db_session, work_order):
    link = work_lifecycle.link_work_order_origin(
        db_session,
        work_order_id=work_order.id,
        origin_type="ticket",
        origin_id=uuid4(),
        contract_name="test.missing_ticket",
    )
    db_session.commit()

    findings = work_lifecycle.dangling_links(db_session)

    assert {
        "link_id": str(link.id),
        "side": "source",
        "entity_type": "ticket",
        "entity_id": str(link.source_id),
        "reason": "missing",
    } in findings


def test_reconcile_heals_pending_selfcare_outcome(db_session, person, monkeypatch):
    subscriber = Subscriber(
        person_id=person.id,
        external_system="selfcare",
        external_id="sub-heal",
        subscriber_number="SUB-HEAL",
    )
    db_session.add(subscriber)
    db_session.flush()
    work_order = workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(
            title="Install needing retry",
            work_type=WorkOrderType.install,
            subscriber_id=subscriber.id,
        ),
    )

    # First completion: the sub push fails, so the outcome is left pending.
    monkeypatch.setattr("app.services.selfcare.notify_work_order_event", lambda *a, **k: False)
    workforce_service.work_orders.update(
        db_session,
        str(work_order.id),
        WorkOrderUpdate(status=WorkOrderStatus.completed),
    )
    outcome = db_session.query(WorkOutcome).filter(WorkOutcome.work_order_id == work_order.id).one()
    assert outcome.status == WorkOutcomeStatus.pending

    # The sub is reachable now: the self-heal sweep flips it to succeeded.
    monkeypatch.setattr("app.services.selfcare.notify_work_order_event", lambda *a, **k: True)
    result = work_lifecycle.reconcile_pending_outcomes(db_session)

    db_session.refresh(outcome)
    assert result["processed"] == 1
    assert result["healed"] == 1
    assert outcome.status == WorkOutcomeStatus.succeeded


def test_reconcile_leaves_outcome_pending_when_push_still_fails(db_session, person, monkeypatch):
    subscriber = Subscriber(
        person_id=person.id,
        external_system="selfcare",
        external_id="sub-stuck",
        subscriber_number="SUB-STUCK",
    )
    db_session.add(subscriber)
    db_session.flush()
    work_order = workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(title="Repair retry", work_type=WorkOrderType.repair, subscriber_id=subscriber.id),
    )
    monkeypatch.setattr("app.services.selfcare.notify_work_order_event", lambda *a, **k: False)
    workforce_service.work_orders.update(
        db_session, str(work_order.id), WorkOrderUpdate(status=WorkOrderStatus.completed)
    )

    result = work_lifecycle.reconcile_pending_outcomes(db_session)

    outcome = db_session.query(WorkOutcome).filter(WorkOutcome.work_order_id == work_order.id).one()
    assert result["healed"] == 0
    assert outcome.status == WorkOutcomeStatus.pending


def test_completion_resolves_ticket_when_enabled(db_session, ticket, monkeypatch):
    import app.services.settings_spec as ss
    from app.models.domain_settings import SettingDomain
    from app.models.tickets import TicketStatus

    original = ss.resolve_value

    def _fake(db, domain, key, **kwargs):
        if domain == SettingDomain.workflow and key == "work_order_completion_resolves_ticket":
            return True
        return original(db, domain, key, **kwargs)

    monkeypatch.setattr(ss, "resolve_value", _fake)
    monkeypatch.setattr("app.services.selfcare.notify_work_order_event", lambda *a, **k: True)

    work_order = workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(title="Fix ONT", work_type=WorkOrderType.repair, ticket_id=ticket.id),
    )
    workforce_service.work_orders.update(
        db_session, str(work_order.id), WorkOrderUpdate(status=WorkOrderStatus.completed)
    )

    db_session.refresh(ticket)
    assert ticket.status == TicketStatus.closed
    link = (
        db_session.query(WorkLink)
        .filter(WorkLink.source_type == WorkEntityType.work_order)
        .filter(WorkLink.source_id == work_order.id)
        .filter(WorkLink.target_type == WorkEntityType.ticket)
        .filter(WorkLink.target_id == ticket.id)
        .filter(WorkLink.link_type == WorkLinkType.resulted_in)
        .one_or_none()
    )
    assert link is not None
    assert link.contract_name == "work_order.completed.resolved_ticket"


def test_completion_leaves_ticket_open_when_disabled(db_session, ticket, monkeypatch):
    from app.models.tickets import TicketStatus

    monkeypatch.setattr("app.services.selfcare.notify_work_order_event", lambda *a, **k: True)
    work_order = workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(title="Fix ONT", work_type=WorkOrderType.repair, ticket_id=ticket.id),
    )
    workforce_service.work_orders.update(
        db_session, str(work_order.id), WorkOrderUpdate(status=WorkOrderStatus.completed)
    )

    db_session.refresh(ticket)
    assert ticket.status != TicketStatus.closed  # default: contract off, no auto-resolve
