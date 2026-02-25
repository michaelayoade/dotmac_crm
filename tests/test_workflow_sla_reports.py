from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.person import Person
from app.models.service_team import ServiceTeam, ServiceTeamType
from app.models.tickets import Ticket
from app.models.workflow import SlaClock, SlaClockStatus, SlaPolicy, WorkflowEntityType
from app.services import workflow as workflow_service


def test_sla_report_summary_aggregates_ticket_breakdowns(db_session):
    team = ServiceTeam(name="SLA Team", team_type=ServiceTeamType.support)
    person = Person(first_name="Sla", last_name="Agent", email="sla-agent@example.com")
    db_session.add_all([team, person])
    db_session.flush()

    ticket_ok = Ticket(title="Ticket OK", service_team_id=team.id, assigned_to_person_id=person.id)
    ticket_bad = Ticket(title="Ticket Bad", service_team_id=team.id, assigned_to_person_id=person.id)
    db_session.add_all([ticket_ok, ticket_bad])
    db_session.flush()

    policy = SlaPolicy(name="Ticket SLA", entity_type=WorkflowEntityType.ticket)
    db_session.add(policy)
    db_session.flush()

    now = datetime.now(UTC)
    db_session.add_all(
        [
            SlaClock(
                policy_id=policy.id,
                entity_type=WorkflowEntityType.ticket,
                entity_id=ticket_ok.id,
                status=SlaClockStatus.running,
                started_at=now - timedelta(hours=2),
                due_at=now + timedelta(hours=1),
            ),
            SlaClock(
                policy_id=policy.id,
                entity_type=WorkflowEntityType.ticket,
                entity_id=ticket_bad.id,
                status=SlaClockStatus.breached,
                started_at=now - timedelta(hours=4),
                due_at=now - timedelta(hours=1),
                breached_at=now - timedelta(minutes=30),
            ),
        ]
    )
    db_session.commit()

    summary = workflow_service.sla_reports.summary(db_session)

    assert summary["total_clocks"] == 2
    assert summary["total_breaches"] == 1
    assert summary["breach_rate"] == 0.5

    by_entity = {item["key"]: item for item in summary["by_entity_type"]}
    assert by_entity["ticket"]["total"] == 2
    assert by_entity["ticket"]["breached"] == 1

    team_key = str(team.id)
    by_team = {item["key"]: item for item in summary["ticket_by_service_team"]}
    assert by_team[team_key]["total"] == 2
    assert by_team[team_key]["breached"] == 1

    assignee_key = str(person.id)
    by_assignee = {item["key"]: item for item in summary["ticket_by_assignee"]}
    assert by_assignee[assignee_key]["total"] == 2
    assert by_assignee[assignee_key]["breached"] == 1


def test_sla_report_trend_daily_groups_by_day(db_session):
    policy = SlaPolicy(name="Trend SLA", entity_type=WorkflowEntityType.ticket)
    db_session.add(policy)
    db_session.flush()

    now = datetime.now(UTC).replace(hour=10, minute=0, second=0, microsecond=0)
    ticket = Ticket(title="Trend Ticket")
    db_session.add(ticket)
    db_session.flush()

    db_session.add_all(
        [
            SlaClock(
                policy_id=policy.id,
                entity_type=WorkflowEntityType.ticket,
                entity_id=ticket.id,
                status=SlaClockStatus.running,
                started_at=now - timedelta(days=1),
                due_at=now + timedelta(days=1),
            ),
            SlaClock(
                policy_id=policy.id,
                entity_type=WorkflowEntityType.ticket,
                entity_id=ticket.id,
                status=SlaClockStatus.breached,
                started_at=now - timedelta(days=1, hours=1),
                due_at=now - timedelta(hours=3),
                breached_at=now - timedelta(hours=2),
            ),
            SlaClock(
                policy_id=policy.id,
                entity_type=WorkflowEntityType.ticket,
                entity_id=ticket.id,
                status=SlaClockStatus.breached,
                started_at=now,
                due_at=now + timedelta(hours=3),
                breached_at=now + timedelta(hours=1),
            ),
        ]
    )
    db_session.commit()

    trend = workflow_service.sla_reports.trend_daily(db_session)

    assert len(trend) == 2
    day_one = trend[0]
    day_two = trend[1]
    assert day_one["total"] == 2
    assert day_one["breached"] == 1
    assert day_two["total"] == 1
    assert day_two["breached"] == 1


def test_sla_report_trend_daily_honors_date_window(db_session):
    policy = SlaPolicy(name="Window SLA", entity_type=WorkflowEntityType.ticket)
    ticket = Ticket(title="Window Ticket")
    db_session.add_all([policy, ticket])
    db_session.flush()

    now = datetime.now(UTC).replace(hour=9, minute=0, second=0, microsecond=0)
    older = now - timedelta(days=4)
    in_window = now - timedelta(days=1)
    db_session.add_all(
        [
            SlaClock(
                policy_id=policy.id,
                entity_type=WorkflowEntityType.ticket,
                entity_id=ticket.id,
                status=SlaClockStatus.breached,
                started_at=older,
                due_at=older + timedelta(hours=2),
                breached_at=older + timedelta(hours=3),
            ),
            SlaClock(
                policy_id=policy.id,
                entity_type=WorkflowEntityType.ticket,
                entity_id=ticket.id,
                status=SlaClockStatus.running,
                started_at=in_window,
                due_at=in_window + timedelta(hours=2),
            ),
        ]
    )
    db_session.commit()

    trend = workflow_service.sla_reports.trend_daily(
        db_session,
        start_at=now - timedelta(days=2),
        end_at=now,
    )

    assert len(trend) == 1
    assert trend[0]["date"] == str((now - timedelta(days=1)).date())
    assert trend[0]["total"] == 1
    assert trend[0]["breached"] == 0
