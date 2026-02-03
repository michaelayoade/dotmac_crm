from app.celery_app import celery_app
from app.db import SessionLocal
from app.services.crm.inbox.notifications import send_reply_reminders


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
