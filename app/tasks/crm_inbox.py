from app.celery_app import celery_app
from app.db import SessionLocal
from app.schemas.crm.inbox import InboxSendRequest
from app.services.crm.inbox.notifications import send_reply_reminders
from app.services.crm.inbox.outbound import TransientOutboundError
from app.services.crm import inbox as inbox_service
from app.services.crm.inbox.outbox import process_outbox_item, list_due_outbox_ids


@celery_app.task(name="app.tasks.crm_inbox.send_reply_reminders")
def send_reply_reminders_task():
    session = SessionLocal()
    try:
        send_reply_reminders(session)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(
    name="app.tasks.crm_inbox.send_outbound_message",
    autoretry_for=(TransientOutboundError,),
    retry_kwargs={"max_retries": 5},
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
)
def send_outbound_message_task(payload: dict, author_id: str | None = None):
    session = SessionLocal()
    try:
        request = InboxSendRequest.model_validate(payload)
        return inbox_service.send_message_with_retry(
            session,
            request,
            author_id=author_id,
            max_attempts=2,
            base_backoff=0.5,
            max_backoff=2.0,
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(
    name="app.tasks.crm_inbox.send_outbox_item",
    autoretry_for=(TransientOutboundError,),
    retry_kwargs={"max_retries": 7},
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
def send_outbox_item_task(outbox_id: str):
    session = SessionLocal()
    try:
        return process_outbox_item(session, outbox_id)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(name="app.tasks.crm_inbox.process_outbox_queue")
def process_outbox_queue_task(limit: int = 50):
    session = SessionLocal()
    try:
        ids = list_due_outbox_ids(session, limit=limit)
        for outbox_id in ids:
            send_outbox_item_task.delay(outbox_id)
        return len(ids)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
