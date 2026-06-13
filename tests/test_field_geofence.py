"""Geofence auto-status: arrival at an assigned job auto-starts it (task #46)."""

from app.models.domain_settings import DomainSetting, SettingDomain, SettingValueType
from app.models.workforce import WorkOrderStatus
from app.services.field import geofence
from app.services.field.location_tracking import field_location_tracking as loc_svc

JOB_LAT, JOB_LNG = 6.5244, 3.3792


def _enable_geofence(db, radius=150):
    db.add(
        DomainSetting(
            domain=SettingDomain.field,
            key="geofence_auto_status_enabled",
            value_type=SettingValueType.json,
            value_json=True,
            is_active=True,
        )
    )
    db.add(
        DomainSetting(
            domain=SettingDomain.field,
            key="geofence_arrival_radius_m",
            value_type=SettingValueType.json,
            value_json=radius,
            is_active=True,
        )
    )
    db.commit()


def _assign_job_at(db, work_order, person, lat=JOB_LAT, lng=JOB_LNG, status=WorkOrderStatus.scheduled):
    work_order.assigned_to_person_id = person.id
    work_order.status = status
    work_order.metadata_ = {"resolved_location": {"latitude": lat, "longitude": lng, "address_text": "site"}}
    db.commit()
    db.refresh(work_order)


def test_haversine_known_distance():
    # ~111.2 km per degree of latitude near the equator.
    d = geofence.haversine_m(0.0, 0.0, 1.0, 0.0)
    assert 110_000 < d < 112_000


def test_arrival_auto_starts_job(db_session, work_order, person):
    _enable_geofence(db_session)
    _assign_job_at(db_session, work_order, person)

    result = loc_svc.record_batch(
        db_session,
        str(person.id),
        [{"latitude": JOB_LAT, "longitude": JOB_LNG}],
    )

    assert len(result["transitions"]) == 1
    assert result["transitions"][0]["work_order_id"] == str(work_order.id)
    assert result["transitions"][0]["event"] == "start"
    db_session.refresh(work_order)
    assert work_order.status == WorkOrderStatus.in_progress
    assert work_order.started_at is not None


def test_far_ping_does_not_start(db_session, work_order, person):
    _enable_geofence(db_session)
    _assign_job_at(db_session, work_order, person)

    result = loc_svc.record_batch(
        db_session,
        str(person.id),
        [{"latitude": JOB_LAT + 1.0, "longitude": JOB_LNG}],  # ~111 km away
    )

    assert result["transitions"] == []
    db_session.refresh(work_order)
    assert work_order.status == WorkOrderStatus.scheduled


def test_disabled_by_default(db_session, work_order, person):
    # No setting rows → geofence disabled → no auto-status.
    _assign_job_at(db_session, work_order, person)
    result = loc_svc.record_batch(db_session, str(person.id), [{"latitude": JOB_LAT, "longitude": JOB_LNG}])
    assert result["transitions"] == []
    db_session.refresh(work_order)
    assert work_order.status == WorkOrderStatus.scheduled


def test_repeated_arrival_pings_fire_once(db_session, work_order, person):
    _enable_geofence(db_session)
    _assign_job_at(db_session, work_order, person)

    first = loc_svc.record_batch(db_session, str(person.id), [{"latitude": JOB_LAT, "longitude": JOB_LNG}])
    assert len(first["transitions"]) == 1

    # A second ping near the same job de-duplicates via the deterministic
    # client_event_id — no second transition.
    second = loc_svc.record_batch(db_session, str(person.id), [{"latitude": JOB_LAT, "longitude": JOB_LNG}])
    assert second["transitions"] == []


def test_only_assigned_tech_triggers(db_session, work_order, person):
    _enable_geofence(db_session)
    # Job assigned to nobody → the tech is not the primary actor → no start.
    work_order.status = WorkOrderStatus.scheduled
    work_order.metadata_ = {"resolved_location": {"latitude": JOB_LAT, "longitude": JOB_LNG}}
    db_session.commit()

    fired = geofence.evaluate(db_session, str(person.id), JOB_LAT, JOB_LNG)
    assert fired == []
