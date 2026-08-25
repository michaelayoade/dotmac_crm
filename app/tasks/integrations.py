import time
from datetime import UTC, datetime, timedelta

from app.celery_app import celery_app
from app.db import SessionLocal
from app.logging import get_logger
from app.metrics import observe_job
from app.models.integration import IntegrationRun, IntegrationRunStatus
from app.services import integration as integration_service
from app.services.common import coerce_uuid


@celery_app.task(
    name="app.tasks.integrations.run_integration_job",
    time_limit=300,
    soft_time_limit=240,
)
def run_integration_job(job_id: str):
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger = get_logger(__name__)
    logger.info("INTEGRATION_JOB_START job_id=%s", job_id)
    try:
        running = (
            session.query(IntegrationRun.id)
            .filter(IntegrationRun.job_id == coerce_uuid(job_id))
            .filter(IntegrationRun.status == IntegrationRunStatus.running)
            .first()
        )
        if running:
            stale_cutoff = datetime.now(UTC) - timedelta(hours=1)
            stale = (
                session.query(IntegrationRun)
                .filter(IntegrationRun.id == running[0])
                .filter(IntegrationRun.status == IntegrationRunStatus.running)
                .filter(IntegrationRun.started_at < stale_cutoff)
                .first()
            )
            if stale:
                stale.status = IntegrationRunStatus.failed
                stale.finished_at = datetime.now(UTC)
                stale.error = "stale run reset by scheduler"
                session.commit()
                logger.info("integration_job_stale_run_reset job_id=%s run_id=%s", job_id, stale.id)
            else:
                status = "skipped"
                logger.info("integration_job_skipped_running job_id=%s", job_id)
                return
        integration_service.integration_jobs.run(session, job_id)
    except Exception:
        status = "error"
        session.rollback()
        raise
    finally:
        session.close()
        duration = time.monotonic() - start
        observe_job("integration_job", status, duration)


@celery_app.task(
    name="app.tasks.integrations.sync_chatwoot",
    time_limit=3600,
    soft_time_limit=3300,
)
def sync_chatwoot(
    max_conversations: int | None = 5000,
    skip_messages: bool = False,
):
    """Sync data from Chatwoot CRM."""
    from app.models.domain_settings import SettingDomain
    from app.services import settings_spec
    from app.services.chatwoot import ChatwootImporter

    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger = get_logger(__name__)
    logger.info(
        "CHATWOOT_SYNC_START max_conversations=%s skip_messages=%s",
        max_conversations,
        skip_messages,
    )

    def _coerce_str(value: object | None) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def _coerce_int(value: object | None, default: int) -> int:
        if isinstance(value, int | str | bytes | bytearray):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default
        return default

    def _coerce_bool(value: object | None, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    try:
        chatwoot_sync_enabled = _coerce_bool(
            settings_spec.resolve_value(session, SettingDomain.integration, "chatwoot_sync_enabled"),
            default=False,
        )
        if not chatwoot_sync_enabled:
            logger.info("CHATWOOT_SYNC_DISABLED")
            return {"success": True, "skipped": True, "reason": "chatwoot_sync_disabled"}

        base_url = _coerce_str(settings_spec.resolve_value(session, SettingDomain.integration, "chatwoot_base_url"))
        access_token = _coerce_str(
            settings_spec.resolve_value(session, SettingDomain.integration, "chatwoot_access_token")
        )
        account_id = _coerce_int(
            settings_spec.resolve_value(session, SettingDomain.integration, "chatwoot_account_id"),
            default=1,
        )

        if not base_url or not access_token:
            logger.warning("CHATWOOT_SYNC_NOT_CONFIGURED")
            return {"success": False, "error": "Chatwoot not configured"}

        importer = ChatwootImporter(
            base_url=base_url,
            access_token=access_token,
            account_id=account_id,
        )
        result = importer.import_all(
            session,
            max_conversations=max_conversations,
            skip_messages=skip_messages,
        )

        logger.info(
            "CHATWOOT_SYNC_COMPLETE contacts_created=%d contacts_updated=%d "
            "conversations_created=%d conversations_updated=%d messages_created=%d errors=%d",
            result.contacts.created,
            result.contacts.updated,
            result.conversations.created,
            result.conversations.updated,
            result.messages.created,
            len(result.error_details),
        )
        if result.error_details:
            status = "partial"
            for error in result.error_details[:10]:
                logger.warning("CHATWOOT_SYNC_ERROR %s", error[:200])
        return result.to_dict()
    except Exception as exc:
        status = "error"
        logger.exception("CHATWOOT_SYNC_FAILED error=%s", str(exc))
        session.rollback()
        raise
    finally:
        session.close()
        duration = time.monotonic() - start
        observe_job("chatwoot_sync", status, duration)

    return {"success": False}
