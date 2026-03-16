from datetime import timedelta

from app.models.projects import ProjectStatus
from app.models.workflow import SlaClock, SlaClockStatus, WorkflowEntityType
from app.schemas.projects import ProjectCreate, ProjectUpdate
from app.services import projects as projects_service


def _latest_project_clock(db_session, project_id):
    return (
        db_session.query(SlaClock)
        .filter(SlaClock.entity_type == WorkflowEntityType.project, SlaClock.entity_id == project_id)
        .order_by(SlaClock.created_at.desc())
        .first()
    )


def test_project_create_auto_starts_project_sla_clock(db_session):
    project = projects_service.projects.create(
        db_session,
        ProjectCreate(
            name="Air Fiber Alpha",
            project_type="air_fiber_installation",
        ),
    )

    clock = _latest_project_clock(db_session, project.id)

    assert clock is not None
    assert clock.status == SlaClockStatus.running
    assert clock.priority == "air_fiber_installation"
    assert clock.started_at == project.start_at
    assert clock.due_at == project.due_at
    assert project.due_at == project.start_at + timedelta(days=3)


def test_project_completion_completes_project_sla_clock(db_session):
    project = projects_service.projects.create(
        db_session,
        ProjectCreate(
            name="Cable Rerun Bravo",
            project_type="cable_rerun",
        ),
    )

    updated = projects_service.projects.update(
        db_session,
        str(project.id),
        ProjectUpdate(status=ProjectStatus.completed),
    )
    clock = _latest_project_clock(db_session, updated.id)

    assert clock is not None
    assert clock.status == SlaClockStatus.completed
    assert clock.completed_at is not None
    assert updated.due_at == updated.start_at + timedelta(days=5)
