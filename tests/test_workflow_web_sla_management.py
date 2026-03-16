"""Tests for workflow SLA management web handlers."""

from __future__ import annotations

import asyncio
import concurrent.futures
from uuid import UUID

from app.models.workflow import SlaBreachStatus, SlaClockStatus, WorkflowEntityType
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


def test_workflow_sla_clock_create_passes_parsed_payload(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_create(*, db, payload):
        captured["db"] = db
        captured["payload"] = payload

    monkeypatch.setattr(system_web.workflow_service.sla_clocks, "create", _fake_create)

    request = _FakeRequest(
        {
            "policy_id": "00000000-0000-0000-0000-000000000001",
            "entity_type": "ticket",
            "entity_id": "00000000-0000-0000-0000-000000000002",
            "priority": "high",
            "started_at": "2026-03-12T10:00:00+00:00",
        }
    )

    response = _run_async(system_web.workflow_sla_clock_create(request, db=object()))

    assert response.status_code == 303
    assert response.headers.get("location") == "/admin/system/workflow"
    payload = captured["payload"]
    assert payload.policy_id == UUID("00000000-0000-0000-0000-000000000001")
    assert payload.entity_type == WorkflowEntityType.ticket
    assert payload.entity_id == UUID("00000000-0000-0000-0000-000000000002")
    assert payload.priority == "high"
    assert str(payload.started_at).startswith("2026-03-12 10:00:00")


def test_workflow_sla_clock_update_passes_status_and_timestamps(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_update(*, db, clock_id, payload):
        captured["db"] = db
        captured["clock_id"] = clock_id
        captured["payload"] = payload

    monkeypatch.setattr(system_web.workflow_service.sla_clocks, "update", _fake_update)

    request = _FakeRequest(
        {
            "status": "paused",
            "due_at": "2026-03-12T12:00:00+00:00",
            "paused_at": "2026-03-12T11:00:00+00:00",
            "total_paused_seconds": "300",
            "completed_at": "",
            "breached_at": "",
        }
    )

    response = _run_async(
        system_web.workflow_sla_clock_update("00000000-0000-0000-0000-000000000003", request, db=object())
    )

    assert response.status_code == 303
    assert captured["clock_id"] == "00000000-0000-0000-0000-000000000003"
    payload = captured["payload"]
    assert payload.status == SlaClockStatus.paused
    assert payload.total_paused_seconds == 300
    assert str(payload.due_at).startswith("2026-03-12 12:00:00")
    assert str(payload.paused_at).startswith("2026-03-12 11:00:00")


def test_workflow_sla_breach_create_passes_optional_breached_at(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_create(*, db, payload):
        captured["db"] = db
        captured["payload"] = payload

    monkeypatch.setattr(system_web.workflow_service.sla_breaches, "create", _fake_create)

    request = _FakeRequest(
        {
            "clock_id": "00000000-0000-0000-0000-000000000004",
            "breached_at": "2026-03-12T10:30:00+00:00",
            "notes": "Manual breach for review",
        }
    )

    response = _run_async(system_web.workflow_sla_breach_create(request, db=object()))

    assert response.status_code == 303
    payload = captured["payload"]
    assert payload.clock_id == UUID("00000000-0000-0000-0000-000000000004")
    assert payload.notes == "Manual breach for review"
    assert str(payload.breached_at).startswith("2026-03-12 10:30:00")


def test_workflow_sla_breach_update_passes_status_and_notes(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_update(*, db, breach_id, payload):
        captured["db"] = db
        captured["breach_id"] = breach_id
        captured["payload"] = payload

    monkeypatch.setattr(system_web.workflow_service.sla_breaches, "update", _fake_update)

    request = _FakeRequest(
        {
            "status": "resolved",
            "notes": "Closed after PM follow-up",
        }
    )

    response = _run_async(
        system_web.workflow_sla_breach_update("00000000-0000-0000-0000-000000000005", request, db=object())
    )

    assert response.status_code == 303
    assert captured["breach_id"] == "00000000-0000-0000-0000-000000000005"
    payload = captured["payload"]
    assert payload.status == SlaBreachStatus.resolved
    assert payload.notes == "Closed after PM follow-up"
