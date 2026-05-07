from app.celery_app import celery_app
from app.db import SessionLocal
from app.schemas.crm.inbox import InboxSendRequest
from app.services.crm import inbox as inbox_service
from app.services.crm.ai_intake import (
    backfill_missing_handoff_states,
    escalate_expired_pending_intakes,
    retry_team_only_ai_assignments,
    send_due_handoff_reassurance_followups,
)
from app.services.crm.inbox.outbound import TransientOutboundError
from app.services.crm.inbox.outbox import cleanup_old_outbox, list_due_outbox_ids, process_outbox_item


@celery_app.task(name="app.tasks.crm_inbox.auto_resolve_idle_conversations")
def auto_resolve_idle_conversations_task():
    """Auto-resolve idle conversations based on configured threshold."""
    import logging
    import time

    from app.metrics import observe_job

    logger = logging.getLogger(__name__)
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger.info("AUTO_RESOLVE_TASK_START")
    try:
        from app.services.crm.inbox.auto_resolve import auto_resolve_idle_conversations

        result = auto_resolve_idle_conversations(session)
        logger.info("AUTO_RESOLVE_TASK_COMPLETE resolved=%s", result.get("resolved", 0))
        return result
    except Exception:
        status = "error"
        session.rollback()
        raise
    finally:
        session.close()
        observe_job("auto_resolve_idle_conversations", status, time.monotonic() - start)


@celery_app.task(name="app.tasks.crm_inbox.send_reply_reminders")
def send_reply_reminders_task():
    # Temporary operational safety switch: disable heavy reminder scanning.
    # Keep this task as a no-op while inbox ingestion backlog is being cleared.
    return 0


@celery_app.task(name="app.tasks.crm_inbox.escalate_expired_ai_intake_conversations")
def escalate_expired_ai_intake_conversations_task(limit: int = 200):
    import logging
    import time

    from app.metrics import observe_job

    logger = logging.getLogger(__name__)
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger.info("AI_INTAKE_MAINTENANCE_START")
    try:
        backfill = backfill_missing_handoff_states(session, limit=max(limit, 500))
        reminders = send_due_handoff_reassurance_followups(session, limit=limit)
        result = escalate_expired_pending_intakes(session, limit=limit)
        logger.info(
            "AI_INTAKE_MAINTENANCE_COMPLETE backfill_updated=%s reminders_sent=%s reminders_suppressed=%s escalated=%s skipped=%s errors=%s",
            backfill.get("updated", 0),
            reminders.get("sent", 0),
            reminders.get("suppressed", 0),
            result.get("escalated", 0),
            result.get("skipped", 0),
            len(reminders.get("errors", [])) + len(result.get("errors", [])),
        )
        return {
            "backfill": backfill,
            "reminders": reminders,
            "escalations": result,
        }
    except Exception:
        status = "error"
        session.rollback()
        raise
    finally:
        session.close()
        observe_job("escalate_expired_ai_intake", status, time.monotonic() - start)


@celery_app.task(name="app.tasks.crm_inbox.retry_team_only_ai_assignments")
def retry_team_only_ai_assignments_task(limit: int = 200):
    import logging
    import time

    from app.metrics import observe_job

    logger = logging.getLogger(__name__)
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger.info("AI_INTAKE_ASSIGNMENT_RETRY_START")
    try:
        result = retry_team_only_ai_assignments(session, limit=limit)
        logger.info(
            "AI_INTAKE_ASSIGNMENT_RETRY_COMPLETE retried=%s assigned=%s skipped=%s errors=%s",
            result.get("retried", 0),
            result.get("assigned", 0),
            result.get("skipped", 0),
            len(result.get("errors", [])),
        )
        return result
    except Exception:
        status = "error"
        session.rollback()
        raise
    finally:
        session.close()
        observe_job("retry_team_only_ai_assignments", status, time.monotonic() - start)


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
        trace_id = None
        if isinstance(payload, dict):
            metadata = payload.get("metadata")
            if isinstance(metadata, dict):
                trace_id = metadata.get("trace_id")
        return inbox_service.send_message_with_retry(
            session,
            request,
            author_id=author_id,
            trace_id=trace_id,
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


@celery_app.task(name="app.tasks.crm_inbox.cleanup_old_outbox")
def cleanup_old_outbox_task(retention_days: int = 7):
    """Remove old terminal outbox records so the failed queue doesn't grow forever."""
    session = SessionLocal()
    try:
        return cleanup_old_outbox(session, retention_days=retention_days)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(name="app.tasks.crm_inbox.check_sla_breaches")
def check_sla_breaches_task():
    """Check for SLA breaches and alert assigned agents."""
    import logging
    import time

    from app.metrics import observe_job

    logger = logging.getLogger(__name__)
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger.info("SLA_BREACH_CHECK_START")
    try:
        from app.services.crm.inbox.sla import check_and_alert_breaches

        result = check_and_alert_breaches(session)
        logger.info(
            "SLA_BREACH_CHECK_COMPLETE response_breaches=%s resolution_breaches=%s alerted=%s",
            result.get("response_breaches", 0),
            result.get("resolution_breaches", 0),
            result.get("alerted_response", 0) + result.get("alerted_resolution", 0),
        )
        return result
    except Exception:
        status = "error"
        session.rollback()
        raise
    finally:
        session.close()
        observe_job("check_sla_breaches", status, time.monotonic() - start)


@celery_app.task(name="app.tasks.crm_inbox.retry_pending_csat_invitations")
def retry_pending_csat_invitations_task(limit: int = 50):
    """Retry CSAT invitations that were created but never delivered.

    Picks up `pending` SurveyInvitation rows where the original send raised
    (e.g. WhatsApp #131000, transient Meta failures). Each retry runs through
    the same outbound circuit breaker and per-channel retry logic.

    Skips invitations younger than 5 minutes (to avoid racing the original
    queue path) and older than 24 hours (to avoid sending CSATs for stale
    conversations).
    """
    import logging
    import time
    from datetime import UTC, datetime, timedelta

    from app.metrics import observe_job
    from app.models.comms import SurveyInvitation, SurveyInvitationStatus

    logger = logging.getLogger(__name__)
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    results = {"retried": 0, "succeeded": 0, "failed": 0, "skipped": 0}
    try:
        from app.services.crm.inbox.csat import retry_pending_invitation

        now = datetime.now(UTC)
        cutoff_recent = now - timedelta(minutes=5)
        cutoff_stale = now - timedelta(hours=24)
        pending = (
            session.query(SurveyInvitation)
            .filter(SurveyInvitation.status == SurveyInvitationStatus.pending)
            .filter(SurveyInvitation.conversation_id.isnot(None))
            .filter(SurveyInvitation.created_at <= cutoff_recent)
            .filter(SurveyInvitation.created_at >= cutoff_stale)
            .order_by(SurveyInvitation.created_at.asc())
            .limit(limit)
            .all()
        )
        for invitation in pending:
            results["retried"] += 1
            try:
                outcome = retry_pending_invitation(session, invitation=invitation)
            except Exception:
                session.rollback()
                results["failed"] += 1
                logger.exception("csat_retry_invitation_unexpected_error invitation_id=%s", invitation.id)
                continue
            if outcome.kind == "queued":
                results["succeeded"] += 1
            elif outcome.kind in ("send_failed", "no_target", "error"):
                results["failed"] += 1
            else:
                results["skipped"] += 1
        logger.info(
            "csat_retry_complete retried=%d succeeded=%d failed=%d skipped=%d",
            results["retried"],
            results["succeeded"],
            results["failed"],
            results["skipped"],
        )
        return results
    except Exception:
        status = "error"
        session.rollback()
        raise
    finally:
        session.close()
        observe_job("crm_inbox_retry_csat", status, time.monotonic() - start)


@celery_app.task(name="app.tasks.crm_inbox.check_conversation_data_quality")
def check_conversation_data_quality_task():
    """Daily check for conversations with missing data fields."""
    import logging
    import time

    from app.metrics import observe_job

    logger = logging.getLogger(__name__)
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger.info("DATA_QUALITY_CHECK_START")
    try:
        from app.services.crm.inbox.data_quality import run_data_quality_check_and_notify

        result = run_data_quality_check_and_notify(session)
        logger.info(
            "DATA_QUALITY_CHECK_COMPLETE missing_first_response=%s missing_tags=%s",
            result.get("missing_first_response", 0),
            result.get("missing_tags", 0),
        )
        return result
    except Exception:
        status = "error"
        session.rollback()
        raise
    finally:
        session.close()
        observe_job("check_conversation_data_quality", status, time.monotonic() - start)
