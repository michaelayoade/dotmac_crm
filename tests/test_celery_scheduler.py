from types import SimpleNamespace

from app import celery_scheduler


def test_refresh_invalidates_celery_heap_after_schedule_merge(monkeypatch):
    scheduler = object.__new__(celery_scheduler.DbScheduler)
    scheduler._last_refresh_at = 0.0
    scheduler._heap = [object()]
    scheduler.app = SimpleNamespace(conf={"beat_refresh_seconds": 30})
    scheduler.data = {}
    refreshed_schedule = {
        "weekly_reporting": {
            "task": "app.tasks.reports.run_weekly_inbound_reporting",
            "schedule": object(),
        }
    }
    merged: list[dict] = []

    monkeypatch.setattr(celery_scheduler.time, "monotonic", lambda: 31.0)
    monkeypatch.setattr(celery_scheduler, "build_beat_schedule", lambda: refreshed_schedule)
    monkeypatch.setattr(scheduler, "merge_inplace", merged.append)

    scheduler._refresh_schedule()

    assert merged == [refreshed_schedule]
    assert scheduler._heap is None
    assert scheduler._last_refresh_at == 31.0
