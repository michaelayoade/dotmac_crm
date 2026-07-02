"""Infrastructure ticket: resolve affected customers (topology + manual) and
fan out notifications on open/resolve."""

import uuid

from app.models.subscriber import Subscriber
from app.models.tickets import TicketStatus
from app.services import infrastructure_tickets as infra_mod
from app.services.infrastructure_tickets import infrastructure_tickets


def _subscriber(db_session, number: str) -> Subscriber:
    sub = Subscriber(
        subscriber_number=number,
        external_system="selfcare",
        external_id=uuid.uuid4().hex[:8],
        is_active=True,
    )
    db_session.add(sub)
    db_session.commit()
    db_session.refresh(sub)
    return sub


def _patch_impact(monkeypatch, subscribers, coverage=None):
    monkeypatch.setattr(
        infra_mod.selfcare,
        "fetch_affected_subscribers",
        lambda db, *, node_id=None, basestation_id=None: {
            "subscribers": subscribers,
            "count": len(subscribers),
            "coverage": coverage or {"has_topology_gaps": False, "resolved_node_count": 1},
        },
    )


def _capture_fanout(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        infra_mod,
        "queue_bulk_subscriber_notifications",
        lambda db, **kw: (
            calls.update(kw)
            or {"queued": len(kw["subscriber_ids"]), "skipped": 0, "selected": len(kw["subscriber_ids"])}
        ),
    )
    return calls


def test_resolve_affected_maps_numbers_and_merges_manual(db_session, monkeypatch):
    s1 = _subscriber(db_session, "SUB-1")
    s2 = _subscriber(db_session, "SUB-2")
    manual = _subscriber(db_session, "SUB-MANUAL")
    _patch_impact(
        monkeypatch,
        [
            {"subscriber_number": "SUB-1", "id": "x"},
            {"subscriber_number": "SUB-2", "id": "y"},
            {"subscriber_number": "SUB-UNKNOWN", "id": "z"},  # not in CRM
        ],
    )

    result = infrastructure_tickets.resolve_affected(
        db_session, node_id="node-1", manual_subscriber_ids=[str(manual.id), str(s1.id)]
    )
    ids = set(result["crm_subscriber_ids"])
    assert ids == {s1.id, s2.id, manual.id}  # deduped (s1 in both topology + manual)
    assert result["unmatched_subscriber_numbers"] == ["SUB-UNKNOWN"]
    assert result["topology_count"] == 3


def test_create_builds_tagged_ticket_and_fans_out(db_session, monkeypatch):
    s1 = _subscriber(db_session, "SUB-10")
    s2 = _subscriber(db_session, "SUB-11")
    _patch_impact(
        monkeypatch,
        [{"subscriber_number": "SUB-10", "id": "a"}, {"subscriber_number": "SUB-11", "id": "b"}],
        coverage={"has_topology_gaps": True, "nodes_without_subscribers": [{"node_id": "n"}]},
    )
    fanout = _capture_fanout(monkeypatch)

    result = infrastructure_tickets.create(
        db_session,
        title="Ikeja cabinet down",
        description="Fiber cut at the Ikeja FDH.",
        node_id="node-1",
        asset_label="FDH Ikeja-3",
    )
    ticket = result["ticket"]
    assert "infrastructure" in (ticket.tags or [])
    assert ticket.ticket_type == "infrastructure"
    meta = ticket.metadata_ or {}
    assert meta["affected_count"] == 2
    assert set(meta["affected_subscriber_ids"]) == {str(s1.id), str(s2.id)}
    assert meta["impact_coverage"]["has_topology_gaps"] is True
    # Fan-out was invoked with exactly the affected CRM subscribers.
    assert set(fanout["subscriber_ids"]) == {s1.id, s2.id}
    assert result["notification"]["queued"] == 2


def test_resolve_closes_ticket_and_notifies_affected(db_session, monkeypatch):
    s1 = _subscriber(db_session, "SUB-20")
    _patch_impact(monkeypatch, [{"subscriber_number": "SUB-20", "id": "a"}])
    _capture_fanout(monkeypatch)
    created = infrastructure_tickets.create(db_session, title="OLT down", node_id="node-2")
    ticket_id = created["ticket"].id

    fanout = _capture_fanout(monkeypatch)
    result = infrastructure_tickets.resolve(db_session, str(ticket_id))
    assert result["ticket"].status == TicketStatus.closed
    assert set(fanout["subscriber_ids"]) == {s1.id}  # notified from the stored affected set


def test_fetch_affected_subscribers_unwraps_envelope(db_session, monkeypatch):
    from app.services import selfcare

    seen = {}

    def _fake(db, method, path, *, params=None, json_body=None):
        seen["path"] = path
        seen["params"] = params
        return {
            "data": {"subscribers": [{"subscriber_number": "S1"}], "count": 1, "coverage": {"has_topology_gaps": False}}
        }

    monkeypatch.setattr(selfcare, "_request_json", _fake)
    out = selfcare.fetch_affected_subscribers(db_session, node_id="n1")
    assert out["count"] == 1
    assert seen["path"] == "/outages/impact"
    assert seen["params"] == {"node_id": "n1"}


def test_create_without_notify_skips_fanout(db_session, monkeypatch):
    _subscriber(db_session, "SUB-30")
    _patch_impact(monkeypatch, [{"subscriber_number": "SUB-30", "id": "a"}])
    fanout = _capture_fanout(monkeypatch)
    result = infrastructure_tickets.create(db_session, title="Planned works", node_id="n", notify=False)
    assert result["notification"] == {"queued": 0, "skipped": 0, "selected": 0}
    assert fanout == {}  # queue_bulk never called
