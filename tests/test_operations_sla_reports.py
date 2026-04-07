from datetime import UTC, datetime, timedelta

from app.models.projects import Project, ProjectStatus, ProjectTask, TaskPriority, TaskStatus
from app.models.tickets import Ticket, TicketPriority, TicketStatus
from app.models.workflow import (
    SlaBreach,
    SlaBreachStatus,
    SlaClock,
    SlaClockStatus,
    SlaPolicy,
    WorkflowEntityType,
)
from app.services.operations_sla_reports import operations_sla_violations_report


def _seed_policy(db_session, name: str, entity_type: WorkflowEntityType) -> SlaPolicy:
    policy = SlaPolicy(name=name, entity_type=entity_type, is_active=True)
    db_session.add(policy)
    db_session.commit()
    db_session.refresh(policy)
    return policy


def test_operations_sla_report_lists_ticket_violations(db_session):
    policy = _seed_policy(db_session, "Ticket Resolution SLA", WorkflowEntityType.ticket)
    ticket = Ticket(
        title="Ticket breach",
        status=TicketStatus.open,
        priority=TicketPriority.high,
        region="Lagos",
    )
    db_session.add(ticket)
    db_session.flush()
    clock = SlaClock(
        policy_id=policy.id,
        entity_type=WorkflowEntityType.ticket,
        entity_id=ticket.id,
        priority="high",
        status=SlaClockStatus.breached,
        started_at=datetime.now(UTC) - timedelta(hours=8),
        due_at=datetime.now(UTC) - timedelta(hours=4),
        breached_at=datetime.now(UTC) - timedelta(hours=3),
    )
    db_session.add(clock)
    db_session.flush()
    breach = SlaBreach(clock_id=clock.id, status=SlaBreachStatus.open, breached_at=clock.breached_at)
    db_session.add(breach)
    db_session.commit()

    records = operations_sla_violations_report.list_records(
        db_session,
        entity_type="ticket",
        region=None,
        start_at=None,
        end_at=None,
    )
    summary = operations_sla_violations_report.summary(
        db_session,
        entity_type="ticket",
        region=None,
        start_at=None,
        end_at=None,
    )

    assert len(records) == 1
    assert records[0]["title"] == "Ticket breach"
    assert records[0]["region"] == "Lagos"
    assert records[0]["detail_url"] == f"/admin/support/tickets/{ticket.id}"
    assert summary["total_violations"] == 1
    assert summary["open_violations"] == 1
    assert summary["regions_affected"] == 1


def test_operations_sla_report_excludes_closed_tickets_when_open_only(db_session):
    policy = _seed_policy(db_session, "Ticket Resolution SLA", WorkflowEntityType.ticket)
    active_ticket = Ticket(
        title="Active ticket breach",
        status=TicketStatus.open,
        priority=TicketPriority.high,
        region="Lagos",
    )
    closed_ticket = Ticket(
        title="Closed ticket breach",
        status=TicketStatus.closed,
        priority=TicketPriority.high,
        region="Lagos",
    )
    db_session.add_all([active_ticket, closed_ticket])
    db_session.flush()

    active_clock = SlaClock(
        policy_id=policy.id,
        entity_type=WorkflowEntityType.ticket,
        entity_id=active_ticket.id,
        priority="high",
        status=SlaClockStatus.breached,
        started_at=datetime.now(UTC) - timedelta(hours=8),
        due_at=datetime.now(UTC) - timedelta(hours=4),
        breached_at=datetime.now(UTC) - timedelta(hours=3),
    )
    db_session.add(active_clock)
    db_session.flush()
    db_session.add(
        SlaBreach(clock_id=active_clock.id, status=SlaBreachStatus.open, breached_at=active_clock.breached_at)
    )

    closed_clock = SlaClock(
        policy_id=policy.id,
        entity_type=WorkflowEntityType.ticket,
        entity_id=closed_ticket.id,
        priority="high",
        status=SlaClockStatus.breached,
        started_at=datetime.now(UTC) - timedelta(hours=9),
        due_at=datetime.now(UTC) - timedelta(hours=5),
        breached_at=datetime.now(UTC) - timedelta(hours=2),
    )
    db_session.add(closed_clock)
    db_session.flush()
    db_session.add(
        SlaBreach(clock_id=closed_clock.id, status=SlaBreachStatus.open, breached_at=closed_clock.breached_at)
    )
    db_session.commit()

    records = operations_sla_violations_report.list_records(
        db_session,
        entity_type="ticket",
        region="Lagos",
        start_at=None,
        end_at=None,
        open_only=True,
    )
    summary = operations_sla_violations_report.summary(
        db_session,
        entity_type="ticket",
        region="Lagos",
        start_at=None,
        end_at=None,
        open_only=True,
    )

    assert len(records) == 1
    assert records[0]["title"] == "Active ticket breach"
    assert summary["total_violations"] == 1
    assert summary["open_violations"] == 1


def test_operations_sla_report_groups_project_violations_by_region(db_session):
    policy = _seed_policy(db_session, "Project Completion SLA", WorkflowEntityType.project)
    first = Project(name="Project One", status=ProjectStatus.active, region="Abuja")
    second = Project(name="Project Two", status=ProjectStatus.active, region="Abuja")
    third = Project(name="Project Three", status=ProjectStatus.active, region="Kaduna")
    db_session.add_all([first, second, third])
    db_session.flush()

    for project in (first, second, third):
        clock = SlaClock(
            policy_id=policy.id,
            entity_type=WorkflowEntityType.project,
            entity_id=project.id,
            priority="project",
            status=SlaClockStatus.breached,
            started_at=datetime.now(UTC) - timedelta(days=10),
            due_at=datetime.now(UTC) - timedelta(days=1),
            breached_at=datetime.now(UTC) - timedelta(hours=6),
        )
        db_session.add(clock)
        db_session.flush()
        db_session.add(SlaBreach(clock_id=clock.id, status=SlaBreachStatus.open, breached_at=clock.breached_at))
    db_session.commit()

    buckets = operations_sla_violations_report.by_region(
        db_session,
        entity_type="project",
        region=None,
        start_at=None,
        end_at=None,
    )

    assert buckets == [
        {"label": "Abuja", "count": 2},
        {"label": "Kaduna", "count": 1},
    ]


def test_operations_sla_report_lists_project_task_records_with_parent_project(db_session):
    policy = _seed_policy(db_session, "Fiber Project Task SLA", WorkflowEntityType.project_task)
    project = Project(name="Fiber Build", status=ProjectStatus.active, region="Port Harcourt")
    db_session.add(project)
    db_session.flush()
    task = ProjectTask(
        project_id=project.id,
        title="Splicing",
        status=TaskStatus.in_progress,
        priority=TaskPriority.high,
    )
    db_session.add(task)
    db_session.flush()
    clock = SlaClock(
        policy_id=policy.id,
        entity_type=WorkflowEntityType.project_task,
        entity_id=task.id,
        priority="high",
        status=SlaClockStatus.breached,
        started_at=datetime.now(UTC) - timedelta(days=2),
        due_at=datetime.now(UTC) - timedelta(hours=8),
        breached_at=datetime.now(UTC) - timedelta(hours=2),
    )
    db_session.add(clock)
    db_session.flush()
    db_session.add(SlaBreach(clock_id=clock.id, status=SlaBreachStatus.acknowledged, breached_at=clock.breached_at))
    db_session.commit()

    records = operations_sla_violations_report.list_records(
        db_session,
        entity_type="project_task",
        region="Port Harcourt",
        start_at=None,
        end_at=None,
    )
    trend = operations_sla_violations_report.trend_daily(
        db_session,
        entity_type="project_task",
        region="Port Harcourt",
        start_at=None,
        end_at=None,
    )

    assert len(records) == 1
    assert records[0]["project"] == "Fiber Build"
    assert records[0]["sla_type"] == "Project Task"
    assert records[0]["detail_url"] == f"/admin/projects/tasks/{task.id}"
    assert len(trend) == 1
    assert trend[0]["count"] == 1


def test_operations_sla_report_region_options_include_configured_regions(db_session):
    policy = _seed_policy(db_session, "Project Completion SLA", WorkflowEntityType.project)
    project = Project(name="Regional Project", status=ProjectStatus.active, region="Port Harcourt")
    db_session.add(project)
    db_session.flush()
    clock = SlaClock(
        policy_id=policy.id,
        entity_type=WorkflowEntityType.project,
        entity_id=project.id,
        priority="project",
        status=SlaClockStatus.breached,
        started_at=datetime.now(UTC) - timedelta(days=3),
        due_at=datetime.now(UTC) - timedelta(days=1),
        breached_at=datetime.now(UTC) - timedelta(hours=5),
    )
    db_session.add(clock)
    db_session.flush()
    db_session.add(SlaBreach(clock_id=clock.id, status=SlaBreachStatus.open, breached_at=clock.breached_at))
    db_session.commit()

    options = operations_sla_violations_report.region_options(db_session, "project")

    assert "Gudu" in options
    assert "Lagos" in options
    assert "Port Harcourt" in options
