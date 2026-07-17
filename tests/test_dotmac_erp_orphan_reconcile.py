"""P5: after a clean FULL sync run, CRM reports the complete seen-id set to
ERP so it can soft-close orphans (canceled/soft-deleted CRM entities silently
drop out of the upsert-only push — no tombstone ever arrives).

The seen sets are built from what the queries RETURNED, not from what synced
successfully (a per-entity push error must not orphan the entity), and the
reconcile is skipped when the run didn't complete cleanly or when a fetch hit
the query limit (truncated set would look like mass cancellation).
"""

from __future__ import annotations

import pytest

from app.services.dotmac_erp import sync as sync_module
from app.services.dotmac_erp.client import DotMacERPError
from app.services.dotmac_erp.sync import DotMacERPSync


class _FakeClient:
    def __init__(self, bulk_error: Exception | None = None, reconcile_error: Exception | None = None):
        self.bulk_calls: list[dict] = []
        self.reconcile_calls: list[dict] = []
        self._bulk_error = bulk_error
        self._reconcile_error = reconcile_error

    def bulk_sync(self, projects=None, tickets=None, work_orders=None):
        if self._bulk_error is not None:
            raise self._bulk_error
        p, t, w = projects or [], tickets or [], work_orders or []
        self.bulk_calls.append({"projects": len(p), "tickets": len(t), "work_orders": len(w)})
        return {
            "projects_synced": len(p),
            "tickets_synced": len(t),
            "work_orders_synced": len(w),
            "errors": [],
        }

    def reconcile_orphans(self, entity_type, seen_ids, active_count):
        if self._reconcile_error is not None:
            raise self._reconcile_error
        call = {"entity_type": entity_type, "seen_ids": list(seen_ids), "active_count": active_count}
        self.reconcile_calls.append(call)
        return {"entity_type": entity_type, "examined": 0, "orphaned": 0, "closed": 0, "skipped_reason": None}


@pytest.fixture()
def reconcile_setting(monkeypatch):
    """Default-ON reconcile setting; tests flip the value via the dict."""
    values = {"dotmac_erp_reconcile_orphans_enabled": True}

    def _resolve(_db, _domain, key, **_kwargs):
        return values.get(key)

    monkeypatch.setattr(sync_module.settings_spec, "resolve_value", _resolve)
    return values


def _service(db_session, client) -> DotMacERPSync:
    svc = DotMacERPSync(db_session)
    svc._get_client = lambda: client  # type: ignore[method-assign]
    return svc


def test_reconcile_called_after_clean_full_run(db_session, project, ticket, work_order, reconcile_setting):
    client = _FakeClient()
    svc = _service(db_session, client)

    result = svc.sync_all_active()

    assert not result.has_errors
    by_type = {c["entity_type"]: c for c in client.reconcile_calls}
    assert set(by_type) == {"project", "ticket", "work_order"}
    # Seen sets contain exactly what the queries returned; active_count matches.
    assert by_type["project"]["seen_ids"] == [str(project.id)]
    assert by_type["project"]["active_count"] == 1
    assert by_type["ticket"]["seen_ids"] == [str(ticket.id)]
    assert by_type["work_order"]["seen_ids"] == [str(work_order.id)]
    # Reconcile happens after the push.
    assert client.bulk_calls, "bulk push should have run first"


def test_reconcile_not_called_when_bulk_errored(db_session, project, reconcile_setting):
    client = _FakeClient(bulk_error=DotMacERPError("boom", status_code=422))
    svc = _service(db_session, client)

    result = svc.sync_all_active()

    assert result.has_errors
    assert any(e.get("type") == "api" for e in result.errors)
    assert client.reconcile_calls == []


def test_reconcile_not_called_when_not_configured(db_session, project, reconcile_setting):
    svc = DotMacERPSync(db_session)
    svc._get_client = lambda: None  # type: ignore[method-assign]

    result = svc.sync_all_active()

    # bulk_sync records a config error; reconcile is skipped on it.
    assert any(e.get("type") == "config" for e in result.errors)


def test_reconcile_disabled_by_setting(db_session, project, reconcile_setting):
    reconcile_setting["dotmac_erp_reconcile_orphans_enabled"] = False
    client = _FakeClient()
    svc = _service(db_session, client)

    result = svc.sync_all_active()

    assert not result.has_errors
    assert client.reconcile_calls == []


def test_reconcile_skips_type_when_fetch_hits_limit(db_session, project, ticket, work_order, reconcile_setting):
    """A fetch that hit ``limit`` may be truncated — absence can't be trusted."""
    client = _FakeClient()
    svc = _service(db_session, client)

    result = svc.sync_all_active(limit=1)

    assert not result.has_errors
    # Every type returned exactly `limit` rows, so every type is skipped.
    assert client.reconcile_calls == []


def test_reconcile_skips_empty_entity_types(db_session, project, reconcile_setting):
    """No tickets/work orders active -> no reconcile round trip for them."""
    client = _FakeClient()
    svc = _service(db_session, client)

    svc.sync_all_active()

    assert [c["entity_type"] for c in client.reconcile_calls] == ["project"]


def test_reconcile_failure_recorded_but_does_not_raise(db_session, project, reconcile_setting):
    client = _FakeClient(reconcile_error=DotMacERPError("reconcile down", status_code=503))
    svc = _service(db_session, client)

    result = svc.sync_all_active()

    assert client.bulk_calls  # push itself succeeded
    reconcile_errors = [e for e in result.errors if e.get("type") == "reconcile"]
    assert len(reconcile_errors) == 1
    assert reconcile_errors[0]["entity_type"] == "project"
