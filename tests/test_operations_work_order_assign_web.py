from __future__ import annotations

import asyncio
import concurrent.futures
from uuid import uuid4

from app.web.admin import operations as operations_web


def _run_async(coro):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro)).result()


class _FakeRequest:
    def __init__(self, form_data: dict[str, str], headers: dict[str, str] | None = None):
        self._form_data = form_data
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
