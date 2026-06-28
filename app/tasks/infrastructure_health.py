import time

from app.celery_app import celery_app
from app.db import SessionLocal
from app.metrics import observe_job


@celery_app.task(name="app.tasks.infrastructure_health.run_infrastructure_health_checks")
def run_infrastructure_health_checks() -> dict:
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    try:
        from app.services import infrastructure_health

        return infrastructure_health.run_health_checks(session)
    except Exception:
        status = "error"
        session.rollback()
        raise
    finally:
        session.close()
        observe_job("run_infrastructure_health_checks", status, time.monotonic() - start)
