from datetime import UTC, datetime
from uuid import uuid4

from app.models.notification import Notification, NotificationChannel
from app.models.person import Person
from app.models.projects import ProjectTask, TaskStatus
from app.models.subscriber import Subscriber
from app.models.workflow import SlaClock, SlaClockStatus, WorkflowEntityType
from app.schemas.projects import ProjectCreate, ProjectTaskUpdate, ProjectUpdate
from app.services import projects as projects_service


def _person(db_session, prefix: str) -> Person:
    person = Person(
        first_name=prefix,
        last_name="User",
        email=f"{prefix.lower()}-{uuid4().hex[:8]}@example.com",
    )
    db_session.add(person)
    db_session.commit()
    db_session.refresh(person)
    return person


def _subscriber_for_person(db_session, person: Person) -> Subscriber:
    subscriber = Subscriber(person_id=person.id, external_system="test", external_id=uuid4().hex)
    db_session.add(subscriber)
    db_session.commit()
    db_session.refresh(subscriber)
    return subscriber


def test_fiber_project_creation_seeds_stage_tasks_and_sla_clocks(db_session):
    customer = _person(db_session, "Customer")
    subscriber = _subscriber_for_person(db_session, customer)

    project = projects_service.projects.create(
        db_session,
        ProjectCreate(
            name="Fiber Install Alpha",
            project_type="fiber_optics_installation",
            subscriber_id=subscriber.id,
        ),
    )

    tasks = (
        db_session.query(ProjectTask)
        .filter(ProjectTask.project_id == project.id, ProjectTask.is_active.is_(True))
        .order_by(ProjectTask.created_at.asc())
        .all()
    )
    assert len(tasks) == 6
    stage_keys = {(task.metadata_ or {}).get("fiber_stage_key") for task in tasks if isinstance(task.metadata_, dict)}
    assert {
        "project_plan",
        "project_survey",
        "drop_cable_installation",
        "survey_approval_po_issuance",
        "last_mile_installation",
        "power_splicing_activation",
    }.issubset(stage_keys)

    clocks = (
        db_session.query(SlaClock)
        .filter(SlaClock.entity_type == WorkflowEntityType.project_task)
        .filter(SlaClock.entity_id.in_([task.id for task in tasks]))
        .all()
    )
    assert len(clocks) >= 6


def test_task_completion_queues_customer_email_and_completes_sla_clock(db_session):
    customer = _person(db_session, "Customer2")
    subscriber = _subscriber_for_person(db_session, customer)
    project = projects_service.projects.create(
        db_session,
        ProjectCreate(
            name="Fiber Install Beta",
            project_type="fiber_optics_installation",
            subscriber_id=subscriber.id,
        ),
    )
    task = (
        db_session.query(ProjectTask)
        .filter(ProjectTask.project_id == project.id)
        .order_by(ProjectTask.created_at.asc())
        .first()
    )
    assert task is not None

    updated = projects_service.project_tasks.update(
        db_session,
        str(task.id),
        ProjectTaskUpdate(status=TaskStatus.done),
    )
    assert updated.status == TaskStatus.done

    clock = (
        db_session.query(SlaClock)
        .filter(SlaClock.entity_type == WorkflowEntityType.project_task, SlaClock.entity_id == task.id)
        .order_by(SlaClock.created_at.desc())
        .first()
    )
    assert clock is not None
    assert clock.status == SlaClockStatus.completed

    customer_email_notifications = (
        db_session.query(Notification)
        .filter(Notification.channel == NotificationChannel.email)
        .filter(Notification.recipient == customer.email)
        .filter(Notification.subject.ilike("%completed%"))
        .all()
    )
    assert customer_email_notifications


def test_sla_breach_notifies_project_roles_and_marks_task(db_session):
    customer = _person(db_session, "Customer3")
    subscriber = _subscriber_for_person(db_session, customer)
    pm = _person(db_session, "PmRole")
    assistant = _person(db_session, "AssistantRole")
    supervisor = _person(db_session, "SupervisorRole")

    project = projects_service.projects.create(
        db_session,
        ProjectCreate(
            name="Fiber Install Gamma",
            project_type="fiber_optics_installation",
            subscriber_id=subscriber.id,
            project_manager_person_id=pm.id,
            assistant_manager_person_id=assistant.id,
            manager_person_id=supervisor.id,
        ),
    )
    task = (
        db_session.query(ProjectTask)
        .filter(ProjectTask.project_id == project.id)
        .order_by(ProjectTask.created_at.asc())
        .first()
    )
    assert task is not None
    clock = (
        db_session.query(SlaClock)
        .filter(SlaClock.entity_type == WorkflowEntityType.project_task, SlaClock.entity_id == task.id)
        .order_by(SlaClock.created_at.desc())
        .first()
    )
    assert clock is not None
    clock.status = SlaClockStatus.breached
    clock.breached_at = datetime.now(UTC)
    db_session.commit()

    projects_service.notify_project_task_sla_breach(db_session, clock)
    db_session.commit()
    db_session.refresh(task)

    assert isinstance(task.metadata_, dict)
    assert task.metadata_.get("sla_breached") is True

    recipients = {pm.email, assistant.email, supervisor.email}
    breach_notifications = (
        db_session.query(Notification)
        .filter(Notification.recipient.in_(list(recipients)))
        .filter(Notification.subject.ilike("%SLA breach%"))
        .all()
    )
    assert len(breach_notifications) >= 3


def test_project_completion_queues_customer_completion_email(db_session):
    customer = _person(db_session, "Customer4")
    subscriber = _subscriber_for_person(db_session, customer)
    project = projects_service.projects.create(
        db_session,
        ProjectCreate(
            name="Fiber Install Delta",
            project_type="fiber_optics_installation",
            subscriber_id=subscriber.id,
        ),
    )

    projects_service.projects.update(
        db_session,
        str(project.id),
        ProjectUpdate(status="completed"),
    )

    completion_notifications = (
        db_session.query(Notification)
        .filter(Notification.channel == NotificationChannel.email)
        .filter(Notification.recipient == customer.email)
        .filter(Notification.subject.ilike("%Project completed%"))
        .all()
    )
    assert completion_notifications
