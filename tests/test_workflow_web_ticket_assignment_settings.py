"""Tests for workflow ticket auto-assignment settings web handler."""

from __future__ import annotations

import asyncio
import concurrent.futures

from app.models.domain_settings import SettingDomain
from app.web.admin import system as system_web


def _run_async(coro):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro)).result()


class _FakeRequest:
    def __init__(self, form_data: dict[str, str]):
        self._form_data = form_data
        self.query_params = {}

    async def form(self):
        return self._form_data


class _CaptureService:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def upsert_by_key(self, _db, key: str, payload):
        self.calls.append((key, payload))


def test_workflow_ticket_assignment_settings_update_persists_values(monkeypatch):
    service = _CaptureService()
    monkeypatch.setitem(system_web.settings_spec.DOMAIN_SETTINGS_SERVICE, SettingDomain.workflow, service)

    request = _FakeRequest(
        {
            "ticket_auto_assignment_enabled": "true",
            "ticket_auto_assign_require_presence": "on",
            "ticket_auto_assign_max_open_tickets": "7",
        }
    )
    response = _run_async(system_web.workflow_ticket_assignment_settings_update(request, db=object()))

    assert response.status_code == 303
    assert response.headers.get("location") == "/admin/system/workflow"
    assert [key for key, _ in service.calls] == [
        "ticket_auto_assignment_enabled",
        "ticket_auto_assign_require_presence",
        "ticket_auto_assign_max_open_tickets",
    ]
    payload_map = {key: payload for key, payload in service.calls}
    assert payload_map["ticket_auto_assignment_enabled"].value_text == "true"
    assert payload_map["ticket_auto_assign_require_presence"].value_text == "true"
    assert payload_map["ticket_auto_assign_max_open_tickets"].value_text == "7"


def test_workflow_ticket_assignment_settings_update_allows_empty_max(monkeypatch):
    service = _CaptureService()
    monkeypatch.setitem(system_web.settings_spec.DOMAIN_SETTINGS_SERVICE, SettingDomain.workflow, service)

    request = _FakeRequest(
        {
            "ticket_auto_assignment_enabled": "",
            "ticket_auto_assign_require_presence": "",
            "ticket_auto_assign_max_open_tickets": "",
        }
    )
    response = _run_async(system_web.workflow_ticket_assignment_settings_update(request, db=object()))

    assert response.status_code == 303
    payload_map = {key: payload for key, payload in service.calls}
    assert payload_map["ticket_auto_assignment_enabled"].value_text == "false"
    assert payload_map["ticket_auto_assign_require_presence"].value_text == "false"
    assert payload_map["ticket_auto_assign_max_open_tickets"].value_text == ""
