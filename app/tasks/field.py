import time

from app.celery_app import celery_app
from app.db import SessionLocal
from app.metrics import observe_job
from app.services.field.location_tracking import field_location_tracking


@celery_app.task(name="app.tasks.field.prune_field_location_pings")
def prune_field_location_pings(older_than_hours: int | None = None) -> dict:
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    try:
        # No explicit override -> use the (settings-resolved) retention window.
        if older_than_hours is None:
            older_than_hours = field_location_tracking.resolved_retention_hours(session)
        deleted = field_location_tracking.prune_pings(session, older_than_hours=older_than_hours)
        return {"deleted": deleted, "older_than_hours": older_than_hours}
    except Exception:
        status = "error"
        session.rollback()
        raise
    finally:
        session.close()
        observe_job("field_location_ping_prune", status, time.monotonic() - start)
