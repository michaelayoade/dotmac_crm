"""recent_tracks builds per-technician breadcrumb trails for the live map,
and ping_history / retention back the admin movement-playback view."""

from datetime import UTC, datetime, timedelta

from app.models.domain_settings import DomainSetting, SettingDomain
from app.services.field.location_tracking import (
    DEFAULT_PING_RETENTION_HOURS,
    RETENTION_SETTING_KEY,
)
from app.services.field.location_tracking import (
    field_location_tracking as svc,
)


def _ping(db, person, lat, lng, *, captured_at, status="on_shift"):
    return svc.record_ping(db, str(person.id), latitude=lat, longitude=lng, captured_at=captured_at, status=status)


def test_recent_tracks_returns_ordered_points_for_sharing_tech(db_session, person):
    svc.set_sharing(db_session, str(person.id), enabled=True, status="on_shift")
    now = datetime.now(UTC)
    _ping(db_session, person, 6.50, 3.40, captured_at=now - timedelta(minutes=20))
    _ping(db_session, person, 6.51, 3.41, captured_at=now - timedelta(minutes=10))
    _ping(db_session, person, 6.52, 3.42, captured_at=now - timedelta(seconds=20))

    tracks = svc.recent_tracks(db_session, window_minutes=30)
    assert len(tracks) == 1
    track = tracks[0]
    assert track["person_id"] == str(person.id)
    # Oldest -> newest so the map can draw the path in order.
    assert [p["latitude"] for p in track["points"]] == [6.50, 6.51, 6.52]


def test_recent_tracks_excludes_points_outside_window(db_session, person):
    svc.set_sharing(db_session, str(person.id), enabled=True, status="on_shift")
    now = datetime.now(UTC)
    _ping(db_session, person, 6.40, 3.30, captured_at=now - timedelta(minutes=90))  # outside window
    _ping(db_session, person, 6.50, 3.40, captured_at=now - timedelta(seconds=20))  # recent

    tracks = svc.recent_tracks(db_session, window_minutes=30)
    assert len(tracks) == 1
    assert [p["latitude"] for p in tracks[0]["points"]] == [6.50]


def test_recent_tracks_excludes_non_sharing_tech(db_session, person):
    svc.set_sharing(db_session, str(person.id), enabled=False)
    _ping(db_session, person, 6.50, 3.40, captured_at=datetime.now(UTC) - timedelta(seconds=10))
    assert svc.recent_tracks(db_session, window_minutes=30) == []


def test_recent_tracks_caps_points_per_tech_keeping_most_recent(db_session, person):
    svc.set_sharing(db_session, str(person.id), enabled=True, status="on_shift")
    now = datetime.now(UTC)
    # Trail ends inside the stale window (last ping ~15s ago) so the tech is live.
    for i in range(10):
        _ping(db_session, person, 6.50 + i * 0.001, 3.40, captured_at=now - timedelta(seconds=(10 - i) * 15))
    tracks = svc.recent_tracks(db_session, window_minutes=30, max_points_per_tech=4)
    points = tracks[0]["points"]
    assert len(points) == 4
    # Kept the four most recent (highest latitudes), still oldest -> newest.
    assert [round(p["latitude"], 3) for p in points] == [6.506, 6.507, 6.508, 6.509]


def test_ping_history_returns_ordered_range_for_one_tech(db_session, person):
    now = datetime.now(UTC)
    _ping(db_session, person, 6.40, 3.30, captured_at=now - timedelta(hours=5))  # before range
    _ping(db_session, person, 6.50, 3.40, captured_at=now - timedelta(hours=2))
    _ping(db_session, person, 6.51, 3.41, captured_at=now - timedelta(hours=1))

    history = svc.ping_history(db_session, str(person.id), since=now - timedelta(hours=3), until=now)
    assert [round(p["latitude"], 2) for p in history] == [6.50, 6.51]  # before-range point excluded, ordered


def test_ping_history_scoped_to_person(db_session, person):
    from app.models.person import Person

    other = Person(first_name="Other", last_name="Tech", email=f"o-{person.id.hex[:6]}@example.com")
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    now = datetime.now(UTC)
    _ping(db_session, person, 6.50, 3.40, captured_at=now - timedelta(minutes=10))
    _ping(db_session, other, 9.99, 9.99, captured_at=now - timedelta(minutes=10))

    history = svc.ping_history(db_session, str(person.id), since=now - timedelta(hours=1), until=now)
    assert all(round(p["latitude"], 2) == 6.50 for p in history)
    assert len(history) == 1


def test_resolved_retention_defaults_to_30_days(db_session):
    assert DEFAULT_PING_RETENTION_HOURS == 720
    assert svc.resolved_retention_hours(db_session) == 720


def test_resolved_retention_honours_domain_setting(db_session):
    db_session.add(
        DomainSetting(
            domain=SettingDomain.field,
            key=RETENTION_SETTING_KEY,
            value_text="2160",
            is_active=True,
        )
    )
    db_session.commit()
    assert svc.resolved_retention_hours(db_session) == 2160
