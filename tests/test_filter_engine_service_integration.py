from __future__ import annotations

from app.schemas.projects import ProjectCreate, ProjectTaskCreate
from app.schemas.tickets import TicketCreate
from app.services import projects as projects_service
from app.services import tickets as tickets_service


def test_tickets_service_list_applies_filters_payload(db_session):
    matching = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Ticket A", status="open", priority="high"),
    )
    tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Ticket B", status="open", priority="low"),
    )

    rows = tickets_service.tickets.list(
        db=db_session,
        subscriber_id=None,
        status=None,
        priority=None,
        channel=None,
        search=None,
        created_by_person_id=None,
        assigned_to_person_id=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=50,
        offset=0,
        filters_payload=[["Ticket", "priority", "=", "high"]],
    )
    assert {row.id for row in rows} == {matching.id}


def test_projects_service_list_applies_filters_payload(db_session):
    matching = projects_service.projects.create(
        db_session,
        ProjectCreate(name="Project A", status="active", priority="high"),
    )
    projects_service.projects.create(
        db_session,
        ProjectCreate(name="Project B", status="active", priority="low"),
    )

    rows = projects_service.projects.list(
        db=db_session,
        subscriber_id=None,
        status=None,
        project_type=None,
        priority=None,
        owner_person_id=None,
        manager_person_id=None,
        project_manager_person_id=None,
        assistant_manager_person_id=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=50,
        offset=0,
        search=None,
        filters_payload=[["Project", "priority", "=", "high"]],
    )
    assert {row.id for row in rows} == {matching.id}


def test_project_tasks_service_list_applies_filters_payload(db_session):
    project = projects_service.projects.create(
        db_session,
        ProjectCreate(name="Rollout", status="active"),
    )
    matching = projects_service.project_tasks.create(
        db_session,
        ProjectTaskCreate(project_id=project.id, title="Task A", status="in_progress", priority="high"),
    )
    projects_service.project_tasks.create(
        db_session,
        ProjectTaskCreate(project_id=project.id, title="Task B", status="blocked", priority="low"),
    )

    rows = projects_service.project_tasks.list(
        db=db_session,
        project_id=None,
        status=None,
        priority=None,
        assigned_to_person_id=None,
        parent_task_id=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=50,
        offset=0,
        include_assigned=False,
        filters_payload=[["Project Task", "priority", "=", "high"]],
    )
    assert {row.id for row in rows} == {matching.id}
