from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from app.services import metrics_snapshot


class _FakeRedis:
    def __init__(self):
        self.values = {}

    def setex(self, key, ttl, value):
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)


def test_database_pressure_snapshot_round_trip(monkeypatch):
    client = _FakeRedis()
    monkeypatch.setattr(metrics_snapshot, "_client", client)

    values = {
        "active": 2,
        "idle": 5,
        "idle_in_transaction": 1,
        "total": 8,
        "oldest_xact_age_seconds": 45.5,
    }
    observed_at = datetime(2026, 7, 14, 5, 0, tzinfo=UTC)

    assert metrics_snapshot.publish_database_pressure_snapshot(values, now=observed_at)
    snapshot = metrics_snapshot.load_database_pressure_snapshot()

    assert snapshot == {
        "domain": "database_pressure",
        "observed_at": observed_at.isoformat(),
        "values": {key: float(value) for key, value in values.items()},
    }


def test_apply_db_runtime_snapshot_marks_missing_snapshot_unavailable(monkeypatch):
    from app import metrics

    available = MagicMock()
    monkeypatch.setattr(metrics, "DB_RUNTIME_SNAPSHOT_AVAILABLE", available)

    metrics.apply_db_runtime_snapshot(None)

    available.set.assert_called_once_with(0)


def test_infrastructure_health_task_publishes_database_snapshot():
    expected_result = {"status": "healthy"}
    expected_snapshot = {
        "active": 2,
        "idle": 5,
        "idle_in_transaction": 1,
        "total": 8,
        "oldest_xact_age_seconds": 45.5,
    }
    session = MagicMock()

    with (
        patch("app.tasks.infrastructure_health.SessionLocal", return_value=session),
        patch(
            "app.services.infrastructure_health.run_health_checks",
            return_value=expected_result,
        ),
        patch(
            "app.tasks.infrastructure_health.collect_db_runtime_snapshot",
            return_value=expected_snapshot,
        ),
        patch(
            "app.services.metrics_snapshot.publish_database_pressure_snapshot",
            return_value=True,
        ) as publish,
        patch("app.tasks.infrastructure_health.observe_job"),
    ):
        from app.tasks.infrastructure_health import run_infrastructure_health_checks

        result = run_infrastructure_health_checks()

    assert result == expected_result
    publish.assert_called_once_with(expected_snapshot)
    session.close.assert_called_once()


def test_infrastructure_snapshot_producer_has_hard_deadline():
    from app.celery_app import celery_app

    task = celery_app.tasks["app.tasks.infrastructure_health.run_infrastructure_health_checks"]
    assert task.soft_time_limit == 50
    assert task.time_limit == 55
