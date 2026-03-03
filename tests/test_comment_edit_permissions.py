import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.models.person import Person
from app.schemas.projects import ProjectCommentCreate
from app.schemas.tickets import TicketCommentCreate
from app.services import projects as projects_service
from app.services import tickets as tickets_service
from app.web import admin as admin_web
from app.web.admin import projects as projects_web
from app.web.admin import tickets as tickets_web


class _FakeRequest:
    def __init__(self, path: str, person_id, form_data: dict[str, str] | None = None):
        self.state = SimpleNamespace(
            user=SimpleNamespace(
                id=uuid4(),
                person_id=person_id,
                first_name="Test",
                last_name="User",
                display_name="Test User",
                email="test@example.com",
            ),
            auth={"person_id": str(person_id)},
        )
        self.headers = {}
        self.url = SimpleNamespace(path=path)
        self.client = SimpleNamespace(host="127.0.0.1")
        self._form_data = form_data or {}

    async def form(self):
        return self._form_data


def test_ticket_comment_edit_requires_comment_author(db_session, ticket, person, monkeypatch):
    other_person = Person(first_name="Other", last_name="User", email="other-ticket@example.com")
    db_session.add(other_person)
    db_session.commit()
    db_session.refresh(other_person)

    comment = tickets_service.ticket_comments.create(
        db_session,
        TicketCommentCreate(ticket_id=ticket.id, author_person_id=person.id, body="Original ticket comment"),
    )

    monkeypatch.setattr(admin_web, "get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr(tickets_web, "_log_activity", lambda **_kwargs: None)

    unauthorized_request = _FakeRequest(
        f"/admin/support/tickets/{ticket.id}/comments/{comment.id}/edit",
        other_person.id,
    )
    monkeypatch.setattr(admin_web, "get_current_user", lambda _request: {"person_id": str(other_person.id)})
    unauthorized_response = tickets_web.edit_ticket_comment(
        unauthorized_request,
        str(ticket.id),
        str(comment.id),
        body="Hacked",
        mentions=None,
        db=db_session,
    )

    assert unauthorized_response.status_code == 403
    db_session.refresh(comment)
    assert comment.body == "Original ticket comment"

    authorized_request = _FakeRequest(
        f"/admin/support/tickets/{ticket.id}/comments/{comment.id}/edit",
        person.id,
    )
    monkeypatch.setattr(admin_web, "get_current_user", lambda _request: {"person_id": str(person.id)})
    authorized_response = tickets_web.edit_ticket_comment(
        authorized_request,
        str(ticket.id),
        str(comment.id),
        body="Updated ticket comment",
        mentions=None,
        db=db_session,
    )

    assert authorized_response.status_code == 303
    db_session.refresh(comment)
    assert comment.body == "Updated ticket comment"


def test_project_comment_edit_requires_comment_author(db_session, project, person, monkeypatch):
    other_person = Person(first_name="Other", last_name="User", email="other-project@example.com")
    db_session.add(other_person)
    db_session.commit()
    db_session.refresh(other_person)

    comment = projects_service.project_comments.create(
        db_session,
        ProjectCommentCreate(project_id=project.id, author_person_id=person.id, body="Original project comment"),
    )

    monkeypatch.setattr(admin_web, "get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr(projects_web, "_log_activity", lambda **_kwargs: None)

    unauthorized_request = _FakeRequest(
        f"/admin/projects/{project.id}/comments/{comment.id}/edit",
        other_person.id,
        {"body": "Hacked", "mentions": "[]"},
    )
    monkeypatch.setattr(admin_web, "get_current_user", lambda _request: {"person_id": str(other_person.id)})
    unauthorized_response = asyncio.run(
        projects_web.project_comment_edit(unauthorized_request, str(project.id), str(comment.id), db=db_session)
    )

    assert unauthorized_response.status_code == 403
    db_session.refresh(comment)
    assert comment.body == "Original project comment"

    authorized_request = _FakeRequest(
        f"/admin/projects/{project.id}/comments/{comment.id}/edit",
        person.id,
        {"body": "Updated project comment", "mentions": "[]"},
    )
    monkeypatch.setattr(admin_web, "get_current_user", lambda _request: {"person_id": str(person.id)})
    authorized_response = asyncio.run(
        projects_web.project_comment_edit(authorized_request, str(project.id), str(comment.id), db=db_session)
    )

    assert authorized_response.status_code == 303
    db_session.refresh(comment)
    assert comment.body == "Updated project comment"
