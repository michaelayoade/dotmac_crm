from __future__ import annotations

import asyncio
import concurrent.futures
from types import SimpleNamespace
from uuid import uuid4

from app.schemas.workforce import WorkOrderCreate
from app.services import ticket_attachments as ticket_attachment_service
from app.services import workforce as workforce_service
from app.web.admin import operations as operations_web
from app.web.admin import projects as projects_web


class _FakeForm(dict):
    def getlist(self, key: str):
        value = self.get(key)
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]


def _run_async(coro):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro)).result()


class _FakeRequest:
    def __init__(self, form_data: dict[str, str], headers: dict[str, str] | None = None):
        self._form_data = _FakeForm(form_data)
        self.headers = headers or {}

    async def form(self):
        return self._form_data


def test_work_order_assign_updates_assignee_and_redirects_to_referrer(monkeypatch):
    calls: list[tuple[str, object]] = []
    order_id = uuid4()
    technician_id = uuid4()

    def _fake_update(_db, work_order_id: str, payload):
        calls.append((work_order_id, payload))

    monkeypatch.setattr(operations_web.workforce_service.work_orders, "update", _fake_update)

    request = _FakeRequest(
        {"assigned_to_person_id": str(technician_id)},
        headers={"referer": "/admin/operations/work-orders?page=2"},
    )

    response = _run_async(operations_web.work_order_assign(request, order_id, db=object()))

    assert response.status_code == 303
    assert response.headers.get("location") == "/admin/operations/work-orders?page=2"
    assert len(calls) == 1
    assert calls[0][0] == str(order_id)
    assert calls[0][1].assigned_to_person_id == technician_id


def test_work_order_assign_allows_unassign(monkeypatch):
    calls: list[tuple[str, object]] = []
    order_id = uuid4()

    def _fake_update(_db, work_order_id: str, payload):
        calls.append((work_order_id, payload))

    monkeypatch.setattr(operations_web.workforce_service.work_orders, "update", _fake_update)

    request = _FakeRequest({"assigned_to_person_id": ""})

    response = _run_async(operations_web.work_order_assign(request, order_id, db=object()))

    assert response.status_code == 303
    assert response.headers.get("location") == "/admin/operations/work-orders"
    assert len(calls) == 1
    assert calls[0][0] == str(order_id)
    assert calls[0][1].assigned_to_person_id is None


def test_work_order_status_update_updates_status_and_redirects_to_referrer(monkeypatch):
    calls: list[tuple[str, object]] = []
    order_id = uuid4()

    def _fake_update(_db, work_order_id: str, payload):
        calls.append((work_order_id, payload))

    monkeypatch.setattr(operations_web.workforce_service.work_orders, "update", _fake_update)

    request = _FakeRequest(
        {"status": "scheduled"},
        headers={"referer": "/admin/operations/work-orders?page=3"},
    )

    response = _run_async(operations_web.work_order_status_update(request, order_id, db=object()))

    assert response.status_code == 303
    assert response.headers.get("location") == "/admin/operations/work-orders?page=3"
    assert len(calls) == 1
    assert calls[0][0] == str(order_id)
    assert calls[0][1].status == operations_web.WorkOrderStatus.scheduled


def test_work_order_note_create_saves_body_and_attachments(monkeypatch):
    calls: list[object] = []
    deleted: list[list[dict]] = []
    order_id = uuid4()
    upload = SimpleNamespace(filename="signal.png")

    def _fake_create(_db, payload):
        calls.append(payload)

    monkeypatch.setattr(operations_web, "get_current_user", lambda _request: {"person_id": str(uuid4())})
    monkeypatch.setattr(operations_web.workforce_service.work_order_notes, "create", _fake_create)
    monkeypatch.setattr(
        ticket_attachment_service,
        "prepare_ticket_attachments",
        lambda uploads: [
            {
                "stored_name": "stored.png",
                "file_name": uploads[0].filename,
                "content": b"image",
            }
        ],
    )
    monkeypatch.setattr(
        ticket_attachment_service,
        "save_ticket_attachments",
        lambda prepared: [
            {
                "file_name": prepared[0]["file_name"],
                "file_size": 5,
                "mime_type": "image/png",
                "key": "uploads/tickets/stored.png",
                "url": "/admin/storage/dotmac-uploads/uploads/tickets/stored.png",
            }
        ],
    )
    monkeypatch.setattr(
        ticket_attachment_service, "delete_ticket_attachments", lambda prepared: deleted.append(prepared)
    )

    request = _FakeRequest(
        {
            "body": "Checked splitter and replaced connector",
            "is_internal": "1",
            "attachments": [upload],
        }
    )

    response = _run_async(operations_web.work_order_note_create(request, order_id, db=object()))

    assert response.status_code == 303
    assert response.headers.get("location") == f"/admin/operations/work-orders/{order_id}"
    assert len(calls) == 1
    payload = calls[0]
    assert payload.work_order_id == order_id
    assert payload.body == "Checked splitter and replaced connector"
    assert payload.is_internal is True
    assert payload.attachments == [
        {
            "file_name": "signal.png",
            "file_size": 5,
            "mime_type": "image/png",
            "key": "uploads/tickets/stored.png",
            "url": "/admin/storage/dotmac-uploads/uploads/tickets/stored.png",
        }
    ]
    assert deleted == []


def test_work_order_new_prefills_from_project(monkeypatch, db_session, project):
    captured = {}

    def _fake_template_response(template_name, context, *args, **kwargs):
        captured["template_name"] = template_name
        captured["context"] = context
        return context

    monkeypatch.setattr(operations_web.templates, "TemplateResponse", _fake_template_response)
    monkeypatch.setattr(operations_web.dispatch_service.technicians, "list", lambda *args, **kwargs: [])
    monkeypatch.setattr(operations_web, "get_current_user", lambda _request: {"roles": ["admin"], "permissions": []})
    monkeypatch.setattr(operations_web, "get_sidebar_stats", lambda _db: {})

    project.description = "Install customer fiber segment"
    db_session.commit()

    context = operations_web.work_order_new(_FakeRequest({}), project_id=str(project.id), db=db_session)

    assert captured["template_name"] == "admin/operations/work_order_form.html"
    assert context["linked_project"].id == project.id
    assert context["form"]["project_id"] == str(project.id)
    assert context["form"]["description"] == "Install customer fiber segment"
    assert context["cancel_url"] == f"/admin/projects/{project.number or project.id}"


def test_work_order_new_prefills_from_project_task(monkeypatch, db_session, project_task):
    captured = {}

    def _fake_template_response(template_name, context, *args, **kwargs):
        captured["template_name"] = template_name
        captured["context"] = context
        return context

    monkeypatch.setattr(operations_web.templates, "TemplateResponse", _fake_template_response)
    monkeypatch.setattr(operations_web.dispatch_service.technicians, "list", lambda *args, **kwargs: [])
    monkeypatch.setattr(operations_web, "get_current_user", lambda _request: {"roles": ["admin"], "permissions": []})
    monkeypatch.setattr(operations_web, "get_sidebar_stats", lambda _db: {})

    project_task.description = "Splice and test the customer segment"
    db_session.commit()

    context = operations_web.work_order_new(_FakeRequest({}), task_id=str(project_task.id), db=db_session)

    assert captured["template_name"] == "admin/operations/work_order_form.html"
    assert context["linked_task"].id == project_task.id
    assert context["form"]["project_id"] == str(project_task.project_id)
    assert context["form"]["project_task_id"] == str(project_task.id)
    assert context["form"]["description"] == "Splice and test the customer segment"
    assert context["cancel_url"] == f"/admin/projects/tasks/{project_task.number or project_task.id}"


def test_work_order_create_links_project_task(monkeypatch, db_session, project_task):
    monkeypatch.setattr(operations_web, "get_current_user", lambda _request: {"roles": ["admin"], "permissions": []})

    response = operations_web.work_order_create(
        _FakeRequest({}),
        title="Task field work",
        description="Field execution for project task",
        status="draft",
        priority="normal",
        work_type="install",
        ticket_id=None,
        subscriber_id=None,
        project_id=str(project_task.project_id),
        project_task_id=str(project_task.id),
        assigned_to_person_id=None,
        scheduled_start=None,
        scheduled_end=None,
        db=db_session,
    )

    db_session.refresh(project_task)

    assert response.status_code == 303
    assert project_task.work_order_id is not None
    assert response.headers["location"] == f"/admin/operations/work-orders/{project_task.work_order_id}"


def test_project_detail_context_includes_linked_work_orders(monkeypatch, db_session, project):
    work_order = workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(title="Project field visit", project_id=project.id),
    )

    def _fake_template_response(_template_name, context, *args, **kwargs):
        return context

    monkeypatch.setattr(projects_web.templates, "TemplateResponse", _fake_template_response)
    monkeypatch.setattr("app.csrf.get_csrf_token", lambda _request: "csrf")
    monkeypatch.setattr("app.web.admin._auth_helpers.get_current_user", lambda _request: {"roles": ["admin"]})
    monkeypatch.setattr("app.web.admin._auth_helpers.get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr("app.services.agent_mentions.list_active_users_for_mentions", lambda _db: [])

    context = projects_web.project_detail(_FakeRequest({}), project.number or str(project.id), db_session)

    assert [item.id for item in context["linked_work_orders"]] == [work_order.id]
