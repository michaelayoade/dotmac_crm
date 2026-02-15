from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.notification import Notification, NotificationChannel
from app.models.person import Person
from app.schemas.projects import ProjectCreate
from app.services import projects as projects_service


def test_project_created_in_app_notification_dedupes_roles_same_person(db_session: Session):
    p = Person(first_name="Role", last_name="Holder", email="roleholder@example.com")
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)

    payload = ProjectCreate(
        name="Fiber Build A",
        customer_address="123 Main St",
        owner_person_id=p.id,
        manager_person_id=p.id,
        project_manager_person_id=p.id,
        assistant_manager_person_id=p.id,
    )

    project = projects_service.projects.create(db=db_session, payload=payload)

    notes = (
        db_session.query(Notification)
        .filter(Notification.channel == NotificationChannel.push)
        .filter(Notification.recipient == "roleholder@example.com")
        .filter(Notification.subject.like("%Fiber Build A%"))
        .all()
    )
    assert len(notes) == 1
    assert notes[0].body
    assert "Project Manager" in notes[0].body
    assert "Site Project Coordinator" in notes[0].body
    assert "Site: 123 Main St" in notes[0].body
    assert "/admin/projects/" in notes[0].body
    assert (project.number or str(project.id)) in notes[0].body
