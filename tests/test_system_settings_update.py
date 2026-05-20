"""Regression tests for generic system settings updates."""

from __future__ import annotations

import asyncio
import concurrent.futures
from types import SimpleNamespace

from starlette.datastructures import URL

from app.models.domain_settings import SettingDomain
from app.web.admin import system as system_web


def _run_async(coro):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro)).result()


class _FakeRequest:
    def __init__(self, form_data: dict[str, str]):
        self._form_data = form_data
        self.query_params = {}
        self.base_url = URL("https://crm.dotmac.io/")
        self.state = SimpleNamespace(auth={}, user=None)
        self.cookies = {"csrf_token": "test"}

    async def form(self):
        return self._form_data


class _CaptureService:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def upsert_by_key(self, _db, key: str, payload):
        self.calls.append((key, payload))


def test_settings_update_workflow_allows_empty_nullable_integer(monkeypatch):
    service = _CaptureService()
    monkeypatch.setitem(system_web.settings_spec.DOMAIN_SETTINGS_SERVICE, SettingDomain.workflow, service)
    monkeypatch.setattr(system_web, "_build_settings_context", lambda _db, domain: {"domain": domain})
    monkeypatch.setattr(
        system_web.templates, "TemplateResponse", lambda *_args, **kwargs: SimpleNamespace(status_code=200)
    )
    monkeypatch.setattr(system_web, "get_current_user", lambda _request: {})
    monkeypatch.setattr(system_web, "get_sidebar_stats", lambda _db: {})

    request = _FakeRequest(
        {
            "domain": "workflow",
            "ticket_auto_assignment_enabled": "",
            "ticket_auto_assign_require_presence": "",
            "ticket_auto_assign_max_open_tickets": "",
            "workqueue.hero_band_size": "6",
        }
    )

    response = _run_async(system_web.settings_update(request, domain="workflow", db=object()))

    assert response.status_code == 200
    payload_map = {key: payload for key, payload in service.calls}
    assert payload_map["ticket_auto_assignment_enabled"].value_text == "false"
    assert payload_map["ticket_auto_assign_require_presence"].value_text == "false"
    assert payload_map["ticket_auto_assign_max_open_tickets"].value_text == ""
