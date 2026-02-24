from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.projects import Project, ProjectTask
from app.models.tickets import Ticket
from app.schemas.projects import ProjectCreate, ProjectTaskCreate
from app.schemas.tickets import TicketCreate
from app.services import projects as projects_service
from app.services import tickets as tickets_service
from app.services.filter_engine import apply_filter_payload


def _create_ticket(db_session, **overrides) -> Ticket:
    payload = TicketCreate(**({"title": "Untitled"} | overrides))
    return tickets_service.tickets.create(db_session, payload)


def _create_project(db_session, **overrides) -> Project:
    payload = ProjectCreate(**({"name": "Untitled Project"} | overrides))
    return projects_service.projects.create(db_session, payload)


def _create_project_task(db_session, project_id, **overrides) -> ProjectTask:
    payload = ProjectTaskCreate(**({"project_id": project_id, "title": "Untitled Task"} | overrides))
    return projects_service.project_tasks.create(db_session, payload)


def test_filter_engine_ticket_and_logic(db_session):
    t1 = _create_ticket(db_session, title="AP Outage", status="open", priority="high")
    _create_ticket(db_session, title="Routine Maintenance", status="open", priority="low")

    filtered = apply_filter_payload(
        db_session.query(Ticket),
        "Ticket",
        [
            ["Ticket", "status", "=", "open"],
            ["Ticket", "priority", "=", "high"],
        ],
    ).all()

    assert [ticket.id for ticket in filtered] == [t1.id]


def test_filter_engine_ticket_and_with_or_group(db_session):
    t1 = _create_ticket(db_session, title="AP Outage", status="open", priority="high")
    t2 = _create_ticket(db_session, title="Core Outage", status="open", priority="urgent")
    _create_ticket(db_session, title="Backlog Item", status="pending", priority="urgent")

    filtered = apply_filter_payload(
        db_session.query(Ticket),
        "Ticket",
        [
            ["Ticket", "status", "=", "open"],
            {"or": [["Ticket", "priority", "=", "high"], ["Ticket", "priority", "=", "urgent"]]},
        ],
    ).all()

    ids = {ticket.id for ticket in filtered}
    assert ids == {t1.id, t2.id}


def test_filter_engine_like_wraps_pattern(db_session):
    t1 = _create_ticket(db_session, title="AP Outage in Zone 1", status="open")
    _create_ticket(db_session, title="Fiber Relocation", status="open")

    filtered = apply_filter_payload(
        db_session.query(Ticket),
        "Ticket",
        [["Ticket", "title", "like", "outage"]],
    ).all()

    assert [ticket.id for ticket in filtered] == [t1.id]


def test_filter_engine_in_operator(db_session):
    t1 = _create_ticket(db_session, title="Open Item", status="open")
    t2 = _create_ticket(db_session, title="Pending Item", status="pending")
    _create_ticket(db_session, title="Closed Item", status="closed")

    filtered = apply_filter_payload(
        db_session.query(Ticket),
        "Ticket",
        [["Ticket", "status", "in", ["open", "pending"]]],
    ).all()

    ids = {ticket.id for ticket in filtered}
    assert ids == {t1.id, t2.id}


def test_filter_engine_is_and_is_not_null(db_session):
    now = datetime.now(UTC)
    t_with_due = _create_ticket(db_session, title="Due Ticket", status="open", due_at=now)
    t_without_due = _create_ticket(db_session, title="No Due Ticket", status="open")

    null_filtered = apply_filter_payload(
        db_session.query(Ticket),
        "Ticket",
        [["Ticket", "due_at", "is", "null"]],
    ).all()
    assert t_without_due.id in {ticket.id for ticket in null_filtered}
    assert t_with_due.id not in {ticket.id for ticket in null_filtered}

    not_null_filtered = apply_filter_payload(
        db_session.query(Ticket),
        "Ticket",
        [["Ticket", "due_at", "is not", None]],
    ).all()
    assert t_with_due.id in {ticket.id for ticket in not_null_filtered}
    assert t_without_due.id not in {ticket.id for ticket in not_null_filtered}


def test_filter_engine_rejects_mixed_doctypes(db_session):
    _create_ticket(db_session, title="Any", status="open")
    with pytest.raises(ValueError, match="Mixed doctypes are not supported"):
        apply_filter_payload(
            db_session.query(Ticket),
            "Ticket",
            [["Project", "status", "=", "active"]],
        ).all()


def test_filter_engine_project_task_filters(db_session):
    project = _create_project(db_session, name="Fiber Rollout", status="active")
    task_in_scope = _create_project_task(
        db_session, project.id, title="Splice A", status="in_progress", priority="high"
    )
    _create_project_task(db_session, project.id, title="Splice B", status="blocked", priority="low")

    filtered = apply_filter_payload(
        db_session.query(ProjectTask),
        "Project Task",
        [
            ["Project Task", "status", "=", "in_progress"],
            ["Project Task", "priority", "=", "high"],
        ],
    ).all()

    assert [task.id for task in filtered] == [task_in_scope.id]
