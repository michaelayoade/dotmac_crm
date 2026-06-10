import logging
import time
from typing import Any

from app.celery_app import celery_app
from app.db import SessionLocal
from app.metrics import observe_job

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.push.send_push_to_person")
def send_push_to_person(person_id: str, title: str, body: str, data: dict | None = None) -> dict[str, Any]:
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger.info("SEND_PUSH_TO_PERSON_START person_id=%s", person_id)
    results: dict[str, Any] = {}

    try:
        from app.services.push import push_sender

        results = push_sender.send_to_person(session, person_id, title=title, body=body, data=data)
        session.commit()
    except Exception:
        status = "error"
        session.rollback()
        raise
    finally:
        session.close()
        observe_job("send_push_to_person", status, time.monotonic() - start)

    logger.info("SEND_PUSH_TO_PERSON_COMPLETE person_id=%s results=%s", person_id, results)
    return results


@celery_app.task(name="app.tasks.push.send_push_to_vendor_user")
def send_push_to_vendor_user(vendor_user_id: str, title: str, body: str, data: dict | None = None) -> dict[str, Any]:
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger.info("SEND_PUSH_TO_VENDOR_USER_START vendor_user_id=%s", vendor_user_id)
    results: dict[str, Any] = {}

    try:
        from app.services.push import push_sender

        results = push_sender.send_to_vendor_user(session, vendor_user_id, title=title, body=body, data=data)
        session.commit()
    except Exception:
        status = "error"
        session.rollback()
        raise
    finally:
        session.close()
        observe_job("send_push_to_vendor_user", status, time.monotonic() - start)

    logger.info("SEND_PUSH_TO_VENDOR_USER_COMPLETE vendor_user_id=%s results=%s", vendor_user_id, results)
    return results
