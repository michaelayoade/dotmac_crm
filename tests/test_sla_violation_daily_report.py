from datetime import UTC, datetime, timedelta

from app.models.domain_settings import DomainSetting, SettingDomain
from app.models.person import Person
from app.models.projects import Project, ProjectStatus
from app.models.service_team import ServiceTeam, ServiceTeamMember, ServiceTeamType
from app.models.tickets import Ticket, TicketPriority, TicketStatus
from app.models.workflow import (
    SlaBreach,
    SlaBreachStatus,
    SlaClock,
    SlaClockStatus,
    SlaPolicy,
    WorkflowEntityType,
)
from app.services.sla_violation_daily_report import (
    REPORT_LAST_SENT_KEY,
    sla_violation_daily_report_service,
)
from app.tasks import workflow as workflow_tasks


def _seed_policy(db_session, name: str, entity_type: WorkflowEntityType) -> SlaPolicy:
    policy = SlaPolicy(name=name, entity_type=entity_type, is_active=True)
    db_session.add(policy)
    db_session.commit()
    db_session.refresh(policy)
    return policy


def _seed_customer_experience_team(db_session) -> Person:
    person = Person(
        first_name="Ada",
        last_name="Support",
        email="ada.support@example.com",
    )
    db_session.add(person)
    db_session.flush()

    team = ServiceTeam(
        name="Customer Experience",
        team_type=ServiceTeamType.support,
        region="Lagos",
        is_active=True,
    )
    db_session.add(team)
    db_session.flush()

    member = ServiceTeamMember(team_id=team.id, person_id=person.id, is_active=True)
    db_session.add(member)
    db_session.commit()
    return person


def test_daily_report_csv_contains_ticket_and_project_sections(db_session):
    _seed_customer_experience_team(db_session)
    ticket_policy = _seed_policy(db_session, "Ticket SLA", WorkflowEntityType.ticket)
    project_policy = _seed_policy(db_session, "Project SLA", WorkflowEntityType.project)

    ticket = Ticket(
        title="Ticket breach",
        status=TicketStatus.open,
        priority=TicketPriority.high,
        region="Lagos",
    )
    project = Project(name="Fiber rollout", status=ProjectStatus.active, region="Abuja")
    db_session.add_all([ticket, project])
    db_session.flush()

    ticket_clock = SlaClock(
        policy_id=ticket_policy.id,
        entity_type=WorkflowEntityType.ticket,
        entity_id=ticket.id,
        priority="high",
        status=SlaClockStatus.breached,
        started_at=datetime.now(UTC) - timedelta(hours=6),
        due_at=datetime.now(UTC) - timedelta(hours=3),
        breached_at=datetime.now(UTC) - timedelta(hours=2),
    )
    project_clock = SlaClock(
        policy_id=project_policy.id,
        entity_type=WorkflowEntityType.project,
        entity_id=project.id,
        priority="project",
        status=SlaClockStatus.breached,
        started_at=datetime.now(UTC) - timedelta(days=2),
        due_at=datetime.now(UTC) - timedelta(hours=5),
        breached_at=datetime.now(UTC) - timedelta(hours=4),
    )
    db_session.add_all([ticket_clock, project_clock])
    db_session.flush()
    db_session.add_all(
        [
            SlaBreach(clock_id=ticket_clock.id, status=SlaBreachStatus.open, breached_at=ticket_clock.breached_at),
            SlaBreach(clock_id=project_clock.id, status=SlaBreachStatus.open, breached_at=project_clock.breached_at),
        ]
    )
    db_session.commit()

    content = sla_violation_daily_report_service.build_csv_content(db_session)

    assert "Tickets" in content
    assert "Projects" in content
    assert "ticket" in content
    assert "project" in content
    assert "Lagos" in content
    assert "Abuja" in content
    assert "time_over_target" in content


def test_daily_report_recipient_lookup_uses_customer_experience_team(db_session):
    person = _seed_customer_experience_team(db_session)

    emails = sla_violation_daily_report_service.list_recipient_emails(db_session)

    assert emails == [person.email]


def test_send_daily_report_task_marks_business_date_once(monkeypatch, db_session):
    _seed_customer_experience_team(db_session)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            current = datetime(2026, 3, 23, 6, 5, tzinfo=UTC)
            if tz is not None:
                return current.astimezone(tz)
            return current

    sent = {}

    def _fake_send_daily_report(db, *, report_date):
        sent["report_date"] = report_date
        return True, None

    monkeypatch.setattr(workflow_tasks, "datetime", _FixedDateTime)
    monkeypatch.setattr(workflow_tasks, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(
        workflow_tasks.sla_violation_daily_report_service.sla_violation_daily_report_service,
        "send_daily_report",
        _fake_send_daily_report,
    )

    result = workflow_tasks.send_daily_sla_violation_report()

    assert result["status"] == "sent"
    assert sent["report_date"] == "2026-03-23"

    setting = (
        db_session.query(DomainSetting)
        .filter(DomainSetting.domain == SettingDomain.notification)
        .filter(DomainSetting.key == REPORT_LAST_SENT_KEY)
        .first()
    )
    assert setting is not None
    assert setting.value_text == "2026-03-23"

    result_again = workflow_tasks.send_daily_sla_violation_report()

    assert result_again["status"] == "already_sent"
