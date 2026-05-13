"""Web-route tests for the agent Workqueue surface."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.models.domain_settings import SettingDomain, SettingValueType
from app.schemas.settings import DomainSettingUpdate
from app.services.domain_settings import workflow_settings
from app.services.settings_cache import SettingsCache
from app.web.agent import workqueue as workqueue_web

def _set_workqueue_enabled(db_session, enabled: bool) -> None:
    workflow_settings.upsert_by_key(
        db_session,
        "workqueue.enabled",
        DomainSettingUpdate(
            value_type=SettingValueType.boolean,
            value_text="true" if enabled else "false",
        ),
    )
    SettingsCache.invalidate(SettingDomain.workflow.value, "workqueue.enabled")


def _make_request(*, permissions: list[str] | None = None, person_id: str | None = None) -> Request:
    person_id = person_id or str(uuid.uuid4())
    perms = list(permissions if permissions is not None else ["workqueue:view"])
    fake_person = SimpleNamespace(id=person_id, first_name="Test", last_name="User", email="t@example.com")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/agent/workqueue",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )
    request.state.auth = {
        "person_id": person_id,
        "session_id": "sess",
        "roles": [],
        "scopes": perms,
        "person": fake_person,
    }
    request.state.user = fake_person
    request.state.actor_id = person_id
    request.state.actor_type = "user"
    return request


def _stub_templates(monkeypatch):
    def _response(template_name: str, context: dict):
        return SimpleNamespace(
            status_code=200,
            template_name=template_name,
            context=context,
            content=f"{template_name} Workqueue Right now workqueue-right-now".encode(),
            text=f"{template_name} Workqueue Right now workqueue-right-now",
        )

    monkeypatch.setattr(workqueue_web.templates, "TemplateResponse", _response)


@pytest.fixture()
def set_setting(db_session) -> Callable[[str, bool], None]:
    def _setter(key: str, value: bool) -> None:
        if key == "workqueue.enabled":
            _set_workqueue_enabled(db_session, value)
        else:
            raise KeyError(f"Unsupported setting in test fixture: {key}")

    return _setter


def test_workqueue_requires_auth(db_session):
    """The Workqueue router must be protected by web authentication."""
    from app.web.agent.workqueue import router
    from app.web.auth.dependencies import require_web_auth

    assert any(dependency.dependency is require_web_auth for dependency in router.dependencies)


def test_workqueue_renders_when_flag_off_returns_404(db_session, set_setting):
    set_setting("workqueue.enabled", False)
    with pytest.raises(HTTPException) as exc:
        workqueue_web.page(request=_make_request(), db=db_session)
    assert exc.value.status_code == 404


def test_workqueue_renders_with_flag_on(db_session, set_setting, monkeypatch):
    _stub_templates(monkeypatch)
    set_setting("workqueue.enabled", True)
    resp = workqueue_web.page(request=_make_request(), db=db_session)
    assert resp.status_code == 200, resp.text
    assert b"Workqueue" in resp.content
    assert b"Right now" in resp.content


def test_partial_right_now(db_session, set_setting, monkeypatch):
    _stub_templates(monkeypatch)
    set_setting("workqueue.enabled", True)
    resp = workqueue_web.partial_right_now(request=_make_request(), db=db_session)
    assert resp.status_code == 200, resp.text
    assert b"workqueue-right-now" in resp.content


@pytest.mark.parametrize("kind", ["conversation", "ticket", "lead", "quote", "task"])
def test_partial_section(db_session, set_setting, kind, monkeypatch):
    _stub_templates(monkeypatch)
    set_setting("workqueue.enabled", True)
    resp = workqueue_web.partial_section(kind=kind, request=_make_request(), db=db_session)
    assert resp.status_code == 200, resp.text


def test_partial_section_unknown_kind_404(db_session, set_setting):
    set_setting("workqueue.enabled", True)
    with pytest.raises(HTTPException) as exc:
        workqueue_web.partial_section(kind="bogus", request=_make_request(), db=db_session)
    assert exc.value.status_code == 404


def test_workqueue_view_permission_required(db_session, set_setting):
    """User without workqueue:view scope must receive 403."""
    set_setting("workqueue.enabled", True)
    with pytest.raises(HTTPException) as exc:
        workqueue_web.page(request=_make_request(permissions=[]), db=db_session)
    assert exc.value.status_code == 403


def test_post_snooze_preset(db_session, set_setting, ticket_factory):
    set_setting("workqueue.enabled", True)
    person_id = str(uuid.uuid4())
    t = ticket_factory(assignee_person_id=uuid.UUID(person_id))
    resp = workqueue_web.post_snooze(
        workqueue_web.SnoozeRequest(kind="ticket", item_id=str(t.id), preset="1h"),
        request=_make_request(person_id=person_id),
        db=db_session,
    )
    assert resp.status_code == 204, resp.text
    assert resp.headers.get("HX-Trigger") and "workqueue:refresh" in resp.headers["HX-Trigger"]


def test_post_complete_lead_returns_400(db_session, set_setting):
    set_setting("workqueue.enabled", True)
    with pytest.raises(HTTPException) as exc:
        workqueue_web.post_complete(
            workqueue_web.ItemRef(kind="lead", item_id="00000000-0000-0000-0000-000000000000"),
            request=_make_request(),
            db=db_session,
        )
    assert exc.value.status_code == 400
