from datetime import UTC, datetime, timedelta

from app.models.field_location import FieldTechLocationPing
from app.models.scheduler import ScheduledTask
from app.services import scheduler_config
from app.tasks import field as field_tasks
from app.tasks.field import prune_field_location_pings


def test_prune_field_location_pings_task_deletes_old_pings(db_session, person, monkeypatch):
    fresh = FieldTechLocationPing(
        person_id=person.id,
        latitude=6.5,
        longitude=3.3,
        captured_at=datetime.now(UTC),
        received_at=datetime.now(UTC),
    )
    old = FieldTechLocationPing(
        person_id=person.id,
        latitude=6.5,
        longitude=3.3,
        captured_at=datetime.now(UTC) - timedelta(hours=100),
        received_at=datetime.now(UTC) - timedelta(hours=100),
    )
    db_session.add_all([fresh, old])
    db_session.commit()
    monkeypatch.setattr(field_tasks, "SessionLocal", lambda: db_session)

    result = prune_field_location_pings.run(older_than_hours=72)

    assert result == {"deleted": 1, "older_than_hours": 72}
    assert db_session.query(FieldTechLocationPing).count() == 1


def test_field_location_retention_task_is_scheduled(db_session, monkeypatch):
    monkeypatch.setattr(scheduler_config, "SessionLocal", lambda: db_session)

    scheduler_config.build_beat_schedule()

    task = (
        db_session.query(ScheduledTask)
        .filter(ScheduledTask.task_name == "app.tasks.field.prune_field_location_pings")
        .one()
    )
    assert task.name == "field_location_ping_retention"
    assert task.enabled is True
    assert task.interval_seconds == 3600
