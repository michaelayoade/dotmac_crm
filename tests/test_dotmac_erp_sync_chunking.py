"""P1: crm->erp batch calls chunk to the ERP's server caps.

bulk_sync (500/entity) and expense-totals (200/ids) previously sent everything
in one call, so a busy window 422'd and the callers swallowed it — syncing /
returning nothing. These verify the chunking + aggregation.
"""

from __future__ import annotations

from app.services.dotmac_erp.sync import (
    _BULK_SYNC_CHUNK,
    _EXPENSE_TOTALS_CHUNK,
    DotMacERPSync,
    SyncResult,
    _chunked,
)


class _FakeClient:
    def __init__(self):
        self.bulk_calls: list[tuple[int, int, int]] = []
        self.expense_calls: list[int] = []

    def bulk_sync(self, projects=None, tickets=None, work_orders=None):
        p, t, w = projects or [], tickets or [], work_orders or []
        self.bulk_calls.append((len(p), len(t), len(w)))
        return {
            "projects_synced": len(p),
            "tickets_synced": len(t),
            "work_orders_synced": len(w),
            "errors": [],
        }

    def get_expense_totals(self, **kwargs):
        ids = next(iter(kwargs.values()))
        self.expense_calls.append(len(ids))
        return {i: {"paid": 1} for i in ids}


def test_chunked_helper():
    assert list(_chunked(list(range(5)), 2)) == [[0, 1], [2, 3], [4]]
    assert list(_chunked([], 3)) == []


def test_send_bulk_single_call_within_cap():
    client, result = _FakeClient(), SyncResult()
    DotMacERPSync._send_bulk(client, [{}] * 10, [{}] * 5, [{}] * 3, result)

    assert client.bulk_calls == [(10, 5, 3)]  # one call
    assert result.projects_synced == 10
    assert result.tickets_synced == 5
    assert result.work_orders_synced == 3


def test_send_bulk_fans_out_over_cap_in_dependency_order():
    client, result = _FakeClient(), SyncResult()
    projects = [{}] * (_BULK_SYNC_CHUNK + 250)  # 750
    tickets = [{}] * (_BULK_SYNC_CHUNK + 1)  # 501

    DotMacERPSync._send_bulk(client, projects, tickets, [], result)

    # projects chunked (500, 250) THEN tickets (500, 1); no work-order calls.
    assert client.bulk_calls == [(500, 0, 0), (250, 0, 0), (0, 500, 0), (0, 1, 0)]
    assert all(
        p <= _BULK_SYNC_CHUNK and t <= _BULK_SYNC_CHUNK and w <= _BULK_SYNC_CHUNK for (p, t, w) in client.bulk_calls
    )
    assert result.projects_synced == 750
    assert result.tickets_synced == 501


def test_expense_totals_chunk_to_200_and_merge():
    svc = DotMacERPSync(db=None)
    client = _FakeClient()
    svc._get_client = lambda: client  # type: ignore[method-assign]

    ids = [f"id-{i}" for i in range(_EXPENSE_TOTALS_CHUNK + 50)]  # 250
    out = svc.get_project_expense_totals(ids)

    assert client.expense_calls == [200, 50]
    assert len(out) == 250


def test_expense_totals_partial_chunk_failure_is_skipped(monkeypatch):
    from app.services.dotmac_erp.client import DotMacERPError

    svc = DotMacERPSync(db=None)
    calls = {"n": 0}

    class _FlakyClient:
        def get_expense_totals(self, **kwargs):
            calls["n"] += 1
            ids = next(iter(kwargs.values()))
            if calls["n"] == 1:
                raise DotMacERPError("boom")
            return {i: {"paid": 1} for i in ids}

    svc._get_client = lambda: _FlakyClient()  # type: ignore[method-assign]
    ids = [f"id-{i}" for i in range(_EXPENSE_TOTALS_CHUNK + 10)]  # 210 -> 2 chunks
    out = svc.get_ticket_expense_totals(ids)

    # first chunk failed and was skipped; second chunk's 10 ids returned
    assert len(out) == 10
