import time

from app.celery_app import celery_app
from app.db import SessionLocal, collect_db_runtime_snapshot
from app.metrics import observe_job


@celery_app.task(
    name="app.tasks.infrastructure_health.run_infrastructure_health_checks",
    soft_time_limit=50,
    time_limit=55,
)
def run_infrastructure_health_checks() -> dict:
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    try:
        from app.services import infrastructure_health
        from app.services.metrics_snapshot import publish_database_pressure_snapshot

        result = infrastructure_health.run_health_checks(session)
        snapshot = collect_db_runtime_snapshot()
        if snapshot is not None:
            publish_database_pressure_snapshot(snapshot)
        return result
    except Exception:
        status = "error"
        session.rollback()
        raise
    finally:
        session.close()
        observe_job("run_infrastructure_health_checks", status, time.monotonic() - start)
