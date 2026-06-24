"""Field-tech location ingest, snapshot, live feed, and retention (task #42)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.models.dispatch import TechnicianProfile
from app.models.field_location import FieldPresenceStatus, FieldTechLocationPing
from app.models.workforce import WorkOrder, WorkOrderStatus, WorkOrderType
from app.services.field.location_tracking import field_location_tracking as svc


def _ping(db, person, **kw):
    return svc.record_ping(
        db,
        str(person.id),
        latitude=kw.get("latitude", 6.5244),
        longitude=kw.get("longitude", 3.3792),
        accuracy_m=kw.get("accuracy_m", 12.0),
        captured_at=kw.get("captured_at"),
        source=kw.get("source", "mobile"),
        status=kw.get("status"),
    )


def test_record_ping_persists_audit_and_updates_snapshot(db_session, person):
    result = _ping(db_session, person, latitude=6.5, longitude=3.3)

    assert result["ping"].latitude == 6.5
    presence = result["presence"]
    assert presence.last_latitude == 6.5
    assert presence.last_longitude == 3.3
    assert presence.last_location_at is not None
    assert presence.last_seen_at is not None

    pings = db_session.query(FieldTechLocationPing).filter_by(person_id=person.id).all()
    assert len(pings) == 1


def test_snapshot_does_not_roll_backwards_on_stale_ping(db_session, person):
    now = datetime.now(UTC)
    _ping(db_session, person, latitude=1.0, longitude=1.0, captured_at=now)
    # An older fix arriving late must not overwrite the newer snapshot.
    _ping(db_session, person, latitude=9.0, longitude=9.0, captured_at=now - timedelta(minutes=10))

    presence = svc.get_or_create_presence(db_session, str(person.id))
    assert presence.last_latitude == 1.0
    assert presence.last_longitude == 1.0
    # Both pings are still recorded in the audit log.
    assert db_session.query(FieldTechLocationPing).filter_by(person_id=person.id).count() == 2


@pytest.mark.parametrize("lat,lng", [(91.0, 0.0), (-91.0, 0.0), (0.0, 181.0), (0.0, -181.0)])
def test_invalid_coordinates_rejected(db_session, person, lat, lng):
    with pytest.raises(HTTPException) as exc:
        svc.record_ping(db_session, str(person.id), latitude=lat, longitude=lng)
    assert exc.value.status_code == 422


def test_record_batch_collects_per_ping_errors(db_session, person):
    result = svc.record_batch(
        db_session,
        str(person.id),
        [
            {"latitude": 6.5, "longitude": 3.3},
            {"latitude": 200.0, "longitude": 3.3},  # bad
            {"latitude": 6.6, "longitude": 3.4},
        ],
    )
    assert result["accepted"] == 2
    assert len(result["errors"]) == 1
    assert result["errors"][0]["index"] == 1


def test_set_sharing_toggles_and_defaults_status_off(db_session, person):
    presence = svc.set_sharing(db_session, str(person.id), enabled=True, status="on_shift")
    assert presence.location_sharing_enabled is True
    assert presence.status == FieldPresenceStatus.on_shift

    presence = svc.set_sharing(db_session, str(person.id), enabled=False)
    assert presence.location_sharing_enabled is False
    assert presence.status == FieldPresenceStatus.off_shift


def test_live_feed_only_shows_sharing_and_fresh(db_session, person):
    # Sharing on + fresh ping → visible.
    svc.set_sharing(db_session, str(person.id), enabled=True, status="on_shift")
    _ping(db_session, person, latitude=6.5, longitude=3.3)

    items = svc.list_live_locations(db_session, stale_after_seconds=120, limit=50)
    ids = {i["person_id"] for i in items}
    assert str(person.id) in ids
    row = next(i for i in items if i["person_id"] == str(person.id))
    assert row["latitude"] == 6.5
    assert row["status"] == "on_shift"

    # Sharing off → hidden.
    svc.set_sharing(db_session, str(person.id), enabled=False)
    items = svc.list_live_locations(db_session, stale_after_seconds=120, limit=50)
    assert str(person.id) not in {i["person_id"] for i in items}


def test_live_feed_excludes_stale(db_session, person):
    svc.set_sharing(db_session, str(person.id), enabled=True, status="on_shift")
    old = datetime.now(UTC) - timedelta(minutes=30)
    _ping(db_session, person, latitude=6.5, longitude=3.3, captured_at=old)

    items = svc.list_live_locations(db_session, stale_after_seconds=120, limit=50)
    assert str(person.id) not in {i["person_id"] for i in items}


def test_tracking_states_include_all_active_technicians_and_work_context(db_session, person):
    technician = TechnicianProfile(person_id=person.id, title="Installer", region="Lekki")
    order = WorkOrder(
        title="Install ONT",
        status=WorkOrderStatus.dispatched,
        work_type=WorkOrderType.install,
        assigned_to_person_id=person.id,
    )
    db_session.add_all([technician, order])
    db_session.commit()

    svc.set_sharing(db_session, str(person.id), enabled=True, status="on_shift")
    _ping(db_session, person, latitude=6.5, longitude=3.3)

    items = svc.list_tracking_states(db_session, stale_after_seconds=120)

    row = next(item for item in items if item["person_id"] == str(person.id))
    assert row["technician_id"] == str(technician.id)
    assert row["person_label"]
    assert row["title"] == "Installer"
    assert row["region"] == "Lekki"
    assert row["location_sharing_enabled"] is True
    assert row["is_live"] is True
    assert row["last_latitude"] == 6.5
    assert row["active_work_order"] == {
        "id": str(order.id),
        "title": "Install ONT",
        "status": "dispatched",
        "work_type": "install",
    }


def test_tracking_states_keep_not_sharing_technicians_visible(db_session, person):
    technician = TechnicianProfile(person_id=person.id, title="Installer")
    db_session.add(technician)
    db_session.commit()

    items = svc.list_tracking_states(db_session, stale_after_seconds=120)

    row = next(item for item in items if item["person_id"] == str(person.id))
    assert row["location_sharing_enabled"] is False
    assert row["is_live"] is False
    assert row["status"] == "off_shift"
    assert row["last_location_at"] is None


def test_prune_pings_removes_only_old(db_session, person):
    _ping(db_session, person)  # fresh
    old_ping = FieldTechLocationPing(
        person_id=person.id,
        latitude=6.5,
        longitude=3.3,
        captured_at=datetime.now(UTC) - timedelta(hours=200),
        received_at=datetime.now(UTC) - timedelta(hours=200),
    )
    db_session.add(old_ping)
    db_session.commit()

    deleted = svc.prune_pings(db_session, older_than_hours=72)
    assert deleted == 1
    assert db_session.query(FieldTechLocationPing).filter_by(person_id=person.id).count() == 1
