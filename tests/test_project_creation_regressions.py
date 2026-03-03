from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models.projects import Project, ProjectTask, ProjectTemplate, ProjectTemplateTask, ProjectType
from app.schemas.projects import ProjectCreate
from app.services import projects as projects_service
from app.services.events.dispatcher import EventDispatcher
from app.services.events.types import Event, EventType


def test_event_dispatcher_rolls_back_failed_handler_session(db_session: Session):
    existing = ProjectTemplate(name="Existing Fiber Template", project_type=ProjectType.air_fiber_installation)
    db_session.add(existing)
    db_session.commit()

    class FailingHandler:
        def handle(self, db, event) -> None:
            db.add(ProjectTemplate(name="Duplicate Fiber Template", project_type=ProjectType.air_fiber_installation))
            db.flush()

    class SucceedingHandler:
        called = False

        def handle(self, db, event) -> None:
            db.add(ProjectTemplate(name="Cable Template", project_type=ProjectType.cable_rerun))
            db.flush()
            self.called = True

    dispatcher = EventDispatcher()
    dispatcher.register_handler(FailingHandler())
    success_handler = SucceedingHandler()
    dispatcher.register_handler(success_handler)

    dispatcher.dispatch(
        db_session,
        Event(event_type=EventType.project_created, payload={"project_id": "test-project"}),
    )

    assert success_handler.called is True
    assert (
        db_session.query(ProjectTemplate)
        .filter(ProjectTemplate.project_type == ProjectType.cable_rerun)
        .count()
        == 1
    )


def test_project_create_persists_template_tasks_before_event_failures(db_session: Session, person):
    template = ProjectTemplate(name="Air Fiber Template", project_type=ProjectType.air_fiber_installation)
    db_session.add(template)
    db_session.commit()
    db_session.refresh(template)

    template_task = ProjectTemplateTask(
        template_id=template.id,
        title="Customer Premise/Radio Installation",
        sort_order=1,
    )
    db_session.add(template_task)
    db_session.commit()

    payload = ProjectCreate(
        name="Air Fiber Installation - Regression",
        project_type=ProjectType.air_fiber_installation,
        project_template_id=template.id,
        owner_person_id=person.id,
    )

    from unittest.mock import patch

    with patch("app.services.projects.emit_event", side_effect=RuntimeError("event failed")):
        with pytest.raises(RuntimeError, match="event failed"):
            projects_service.projects.create(db=db_session, payload=payload)

    project = db_session.query(Project).filter(Project.name == payload.name).one()
    tasks = db_session.query(ProjectTask).filter(ProjectTask.project_id == project.id).all()
    assert len(tasks) == 1
    assert tasks[0].template_task_id == template_task.id
