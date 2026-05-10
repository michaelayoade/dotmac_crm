"""Web-route tests for the agent Workqueue surface."""

from __future__ import annotations

import asyncio
import concurrent.futures
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request

from app.models.domain_settings import SettingDomain, SettingValueType
from app.schemas.settings import DomainSettingUpdate
from app.services.domain_settings import workflow_settings
from app.services.settings_cache import SettingsCache


def _run_async(coro):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro)).result()


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


@contextmanager
def _build_app(
    db_session,
    *,
    permissions: list[str] | None = None,
    person_id: str | None = None,
) -> Iterator[FastAPI]:
    """Build a minimal FastAPI app mounting only the workqueue router with auth bypassed."""
    from app.web.agent.workqueue import _get_db, router
    from app.web.auth.dependencies import require_web_auth

    person_id = person_id or str(uuid.uuid4())
    perms = list(permissions if permissions is not None else ["workqueue:view"])

    fake_person = SimpleNamespace(
        id=person_id, first_name="Test", last_name="User", email="t@example.com"
    )

    def _override_auth(request: Request):
        auth_info = {
            "person_id": person_id,
            "session_id": "sess",
            "roles": [],
            "scopes": perms,
            "person": fake_person,
        }
        request.state.auth = auth_info
        request.state.user = fake_person
        request.state.actor_id = person_id
        request.state.actor_type = "user"
        return auth_info

    def _override_db():
        try:
            yield db_session
        finally:
            pass

    app = FastAPI()
    app.dependency_overrides[require_web_auth] = _override_auth
    app.dependency_overrides[_get_db] = _override_db
    app.include_router(router)
    try:
        yield app
    finally:
        app.dependency_overrides.clear()


async def _aget(app: FastAPI, path: str, **kwargs: Any) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(path, **kwargs)


@pytest.fixture()
def set_setting(db_session) -> Callable[[str, bool], None]:
    def _setter(key: str, value: bool) -> None:
        if key == "workqueue.enabled":
            _set_workqueue_enabled(db_session, value)
        else:
            raise KeyError(f"Unsupported setting in test fixture: {key}")
    return _setter


def test_workqueue_requires_auth(db_session):
    """Without overriding auth, the route must reject unauthenticated requests."""
    from app.errors import register_error_handlers
    from app.web.agent.workqueue import _get_db, router

    def _override_db():
        try:
            yield db_session
        finally:
            pass

    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[_get_db] = _override_db
    app.include_router(router)
    resp = _run_async(_aget(app, "/agent/workqueue", follow_redirects=False))
    assert resp.status_code in (401, 403, 302, 303, 307)


def test_workqueue_renders_when_flag_off_returns_404(db_session, set_setting):
    set_setting("workqueue.enabled", False)
    with _build_app(db_session) as app:
        resp = _run_async(_aget(app, "/agent/workqueue"))
    assert resp.status_code == 404


def test_workqueue_renders_with_flag_on(db_session, set_setting):
    set_setting("workqueue.enabled", True)
    with _build_app(db_session) as app:
        resp = _run_async(_aget(app, "/agent/workqueue"))
    assert resp.status_code == 200, resp.text
    assert b"Workqueue" in resp.content
    assert b"Right now" in resp.content


def test_partial_right_now(db_session, set_setting):
    set_setting("workqueue.enabled", True)
    with _build_app(db_session) as app:
        resp = _run_async(_aget(app, "/agent/workqueue/_right_now"))
    assert resp.status_code == 200, resp.text
    assert b"workqueue-right-now" in resp.content


@pytest.mark.parametrize("kind", ["conversation", "ticket", "lead", "quote", "task"])
def test_partial_section(db_session, set_setting, kind):
    set_setting("workqueue.enabled", True)
    with _build_app(db_session) as app:
        resp = _run_async(_aget(app, f"/agent/workqueue/_section/{kind}"))
    assert resp.status_code == 200, resp.text


def test_partial_section_unknown_kind_404(db_session, set_setting):
    set_setting("workqueue.enabled", True)
    with _build_app(db_session) as app:
        resp = _run_async(_aget(app, "/agent/workqueue/_section/bogus"))
    assert resp.status_code == 404


def test_workqueue_view_permission_required(db_session, set_setting):
    """User without workqueue:view scope must receive 403."""
    set_setting("workqueue.enabled", True)
    with _build_app(db_session, permissions=[]) as app:
        resp = _run_async(_aget(app, "/agent/workqueue"))
    assert resp.status_code == 403
