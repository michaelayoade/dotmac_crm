"""API for creating/resolving an infrastructure ticket from the CRM."""

import uuid

import pytest
from fastapi import HTTPException

from app.api.tickets import (
    create_infrastructure_ticket,
    preview_infrastructure_impact,
    resolve_infrastructure_ticket,
)
from app.models.subscriber import Subscriber
from app.models.tickets import TicketStatus
from app.schemas.tickets import InfrastructureTicketCreate, InfrastructureTicketResolveRequest
from app.services import infrastructure_tickets as infra_mod


def _subscriber(db_session, number: str) -> Subscriber:
    sub = Subscriber(
        subscriber_number=number, external_system="selfcare", external_id=uuid.uuid4().hex[:8], is_active=True
    )
    db_session.add(sub)
    db_session.commit()
    db_session.refresh(sub)
    return sub


@pytest.fixture()
def _mocked_impact(monkeypatch):
    def _set(subscribers, coverage=None):
        monkeypatch.setattr(
            infra_mod.selfcare,
            "fetch_affected_subscribers",
            lambda db, *, node_id=None, basestation_id=None, olt_id=None, pon_port_id=None: {
                "subscribers": subscribers,
                "count": len(subscribers),
                "coverage": coverage or {"has_topology_gaps": False},
            },
        )
        monkeypatch.setattr(
            infra_mod,
            "queue_bulk_subscriber_notifications",
            lambda db, **kw: {"queued": len(kw["subscriber_ids"]), "skipped": 0, "selected": len(kw["subscriber_ids"])},
        )

    return _set


def test_preview_requires_an_asset(db_session):
    with pytest.raises(HTTPException) as exc:
        preview_infrastructure_impact(node_id=None, basestation_id=None, olt_id=None, pon_port_id=None, db=db_session)
    assert exc.value.status_code == 400


def test_preview_returns_impact_summary(db_session, _mocked_impact):
    s1 = _subscriber(db_session, "SUB-1")
    _mocked_impact(
        [{"subscriber_number": "SUB-1"}, {"subscriber_number": "SUB-X"}], coverage={"has_topology_gaps": True}
    )
    out = preview_infrastructure_impact(
        node_id="node-1", basestation_id=None, olt_id=None, pon_port_id=None, db=db_session
    )
    assert out["affected_count"] == 1  # only SUB-1 matched in CRM
    assert out["topology_count"] == 2
    assert out["unmatched_subscriber_numbers"] == ["SUB-X"]
    assert out["coverage"]["has_topology_gaps"] is True
    del s1


def test_create_requires_an_asset_or_manual(db_session):
    payload = InfrastructureTicketCreate(title="X")
    with pytest.raises(HTTPException) as exc:
        create_infrastructure_ticket(payload=payload, db=db_session, auth=None)
    assert exc.value.status_code == 400


def test_create_infrastructure_ticket_and_notify(db_session, person, _mocked_impact):
    s1 = _subscriber(db_session, "SUB-10")
    s2 = _subscriber(db_session, "SUB-11")
    _mocked_impact([{"subscriber_number": "SUB-10"}, {"subscriber_number": "SUB-11"}])

    payload = InfrastructureTicketCreate(
        title="Ikeja cabinet down", description="Fiber cut.", node_id="node-1", asset_label="FDH Ikeja-3"
    )
    out = create_infrastructure_ticket(payload=payload, db=db_session, auth={"person_id": str(person.id)})
    assert "infrastructure" in (out["ticket"].tags or [])
    assert out["impact"]["affected_count"] == 2
    assert out["notification"]["queued"] == 2
    del s1, s2


def test_create_by_olt_asset(db_session, person, _mocked_impact):
    _subscriber(db_session, "SUB-40")
    _mocked_impact([{"subscriber_number": "SUB-40"}])
    out = create_infrastructure_ticket(
        payload=InfrastructureTicketCreate(title="OLT Ikeja down", olt_id="olt-1"),
        db=db_session,
        auth={"person_id": str(person.id)},
    )
    assert out["ticket"].metadata_["asset"]["olt_id"] == "olt-1"
    assert out["impact"]["affected_count"] == 1


def test_list_assets_endpoint(db_session, monkeypatch):
    from app.api import tickets as tickets_api
    from app.services import selfcare

    monkeypatch.setattr(
        selfcare,
        "fetch_infrastructure_assets",
        lambda db, *, q=None: [{"id": "olt-1", "type": "olt", "label": "OLT-Ikeja (huawei)"}],
    )
    out = tickets_api.list_infrastructure_assets(q=None, db=db_session)
    assert out["items"][0]["type"] == "olt"


def test_resolve_infrastructure_ticket(db_session, person, _mocked_impact):
    _subscriber(db_session, "SUB-20")
    _mocked_impact([{"subscriber_number": "SUB-20"}])
    created = create_infrastructure_ticket(
        payload=InfrastructureTicketCreate(title="OLT down", node_id="node-2"),
        db=db_session,
        auth={"person_id": str(person.id)},
    )
    ticket_id = str(created["ticket"].id)

    out = resolve_infrastructure_ticket(
        ticket_id=ticket_id,
        payload=InfrastructureTicketResolveRequest(),
        db=db_session,
        auth={"person_id": str(person.id)},
    )
    assert out["ticket"].status == TicketStatus.closed
    assert out["notification"]["queued"] == 1
