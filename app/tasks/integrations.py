import time
from datetime import UTC, datetime, timedelta

from app.celery_app import celery_app
from app.db import SessionLocal
from app.logging import get_logger
from app.metrics import observe_job
from app.models.integration import IntegrationRun, IntegrationRunStatus
from app.services import integration as integration_service
from app.services.common import coerce_uuid
from app.services.dotmac_erp import DotMacERPTransientError


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
    name="app.tasks.integrations.sync_dotmac_erp",
    time_limit=600,
    soft_time_limit=540,
)
def sync_dotmac_erp(mode: str = "recently_updated", since_minutes: int = 60):
    """
    Sync data to DotMac ERP.

    Args:
        mode: Sync mode - "recently_updated" (default) or "all_active"
        since_minutes: For recently_updated mode, look back period in minutes

    Returns:
        Dict with sync result summary
    """
    from app.services.dotmac_erp import dotmac_erp_sync, record_sync_result

    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger = get_logger(__name__)
    logger.info("DOTMAC_ERP_SYNC_START mode=%s since_minutes=%s", mode, since_minutes)

    try:
        sync_service = dotmac_erp_sync(session)

        if mode == "all_active":
            result = sync_service.sync_all_active()
        else:
            result = sync_service.sync_recently_updated(since_minutes=since_minutes)

        sync_service.close()

        logger.info(
            "DOTMAC_ERP_SYNC_COMPLETE projects=%d tickets=%d work_orders=%d errors=%d duration=%.2fs",
            result.projects_synced,
            result.tickets_synced,
            result.work_orders_synced,
            len(result.errors),
            result.duration_seconds,
        )

        if result.has_errors:
            status = "partial"
            for error in result.errors:
                logger.warning("DOTMAC_ERP_SYNC_ERROR %s", error)

        # Record stats for dashboard
        record_sync_result(result, mode=mode)

        return {
            "projects_synced": result.projects_synced,
            "tickets_synced": result.tickets_synced,
            "work_orders_synced": result.work_orders_synced,
            "total_synced": result.total_synced,
            "errors": result.errors,
            "duration_seconds": result.duration_seconds,
        }

    except Exception as e:
        status = "error"
        logger.exception("DOTMAC_ERP_SYNC_FAILED error=%s", str(e))
        session.rollback()
        raise

    finally:
        session.close()
        duration = time.monotonic() - start
        observe_job("dotmac_erp_sync", status, duration)


@celery_app.task(
    name="app.tasks.integrations.sync_dotmac_erp_inventory",
    time_limit=600,
    soft_time_limit=540,
)
def sync_dotmac_erp_inventory():
    """
    Pull inventory data from DotMac ERP.

    Syncs items, locations, and stock levels.

    Returns:
        Dict with sync result summary
    """
    from app.services.dotmac_erp import dotmac_erp_inventory_sync, record_inventory_sync_result

    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger = get_logger(__name__)
    logger.info("DOTMAC_ERP_INVENTORY_SYNC_START")

    try:
        sync_service = dotmac_erp_inventory_sync(session)
        result = sync_service.sync_all()
        sync_service.close()

        logger.info(
            "DOTMAC_ERP_INVENTORY_SYNC_COMPLETE items_created=%d items_updated=%d "
            "locations_created=%d locations_updated=%d stock_updated=%d errors=%d duration=%.2fs",
            result.items_created,
            result.items_updated,
            result.locations_created,
            result.locations_updated,
            result.stock_updated,
            len(result.errors),
            result.duration_seconds,
        )

        if result.has_errors:
            status = "partial"
            for error in result.errors[:10]:  # Log first 10 errors
                logger.warning("DOTMAC_ERP_INVENTORY_SYNC_ERROR %s", error)

        # Record stats for dashboard
        record_inventory_sync_result(
            items_created=result.items_created,
            items_updated=result.items_updated,
            locations_created=result.locations_created,
            locations_updated=result.locations_updated,
            stock_updated=result.stock_updated,
            errors=result.errors,
            duration_seconds=result.duration_seconds,
        )

        return {
            "items_created": result.items_created,
            "items_updated": result.items_updated,
            "locations_created": result.locations_created,
            "locations_updated": result.locations_updated,
            "stock_updated": result.stock_updated,
            "total_synced": result.total_synced,
            "errors": result.errors,
            "duration_seconds": result.duration_seconds,
        }

    except Exception as e:
        status = "error"
        logger.exception("DOTMAC_ERP_INVENTORY_SYNC_FAILED error=%s", str(e))
        session.rollback()
        raise

    finally:
        session.close()
        duration = time.monotonic() - start
        observe_job("dotmac_erp_inventory_sync", status, duration)


@celery_app.task(
    name="app.tasks.integrations.sync_dotmac_erp_entity",
    bind=True,
    time_limit=60,
    soft_time_limit=45,
    max_retries=5,
    autoretry_for=(DotMacERPTransientError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
def sync_dotmac_erp_entity(self, entity_type: str, entity_id: str):
    """
    Sync a single entity to DotMac ERP.

    Args:
        entity_type: "project", "ticket", or "work_order"
        entity_id: UUID of the entity to sync

    Returns:
        Dict with sync result
    """
    from app.models.projects import Project
    from app.models.tickets import Ticket
    from app.models.workforce import WorkOrder
    from app.services.dotmac_erp import (
        DotMacERPAuthError,
        DotMacERPError,
        DotMacERPNotFoundError,
        DotMacERPRateLimitError,
        dotmac_erp_sync,
    )

    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger = get_logger(__name__)
    logger.info(
        "DOTMAC_ERP_ENTITY_SYNC_START entity_type=%s entity_id=%s",
        entity_type,
        entity_id,
    )

    sync_service = dotmac_erp_sync(session)
    try:
        entity_uuid = coerce_uuid(entity_id)
        result = None

        if entity_type == "project":
            project = session.get(Project, entity_uuid)
            if project:
                result = sync_service.sync_project(project)
            else:
                logger.warning(
                    "DOTMAC_ERP_ENTITY_SYNC_NOT_FOUND entity_type=%s entity_id=%s",
                    entity_type,
                    entity_id,
                )
                status = "not_found"
                return {
                    "success": False,
                    "error": "Project not found",
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "error_type": "not_found",
                }
        elif entity_type == "ticket":
            ticket = session.get(Ticket, entity_uuid)
            if ticket:
                result = sync_service.sync_ticket(ticket)
            else:
                logger.warning(
                    "DOTMAC_ERP_ENTITY_SYNC_NOT_FOUND entity_type=%s entity_id=%s",
                    entity_type,
                    entity_id,
                )
                status = "not_found"
                return {
                    "success": False,
                    "error": "Ticket not found",
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "error_type": "not_found",
                }
        elif entity_type == "work_order":
            work_order = session.get(WorkOrder, entity_uuid)
            if work_order:
                result = sync_service.sync_work_order(work_order)
            else:
                logger.warning(
                    "DOTMAC_ERP_ENTITY_SYNC_NOT_FOUND entity_type=%s entity_id=%s",
                    entity_type,
                    entity_id,
                )
                status = "not_found"
                return {
                    "success": False,
                    "error": "Work order not found",
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "error_type": "not_found",
                }
        else:
            logger.error("DOTMAC_ERP_ENTITY_SYNC_INVALID_TYPE entity_type=%s", entity_type)
            status = "error"
            return {
                "success": False,
                "error": f"Invalid entity type: {entity_type}",
                "entity_type": entity_type,
                "entity_id": entity_id,
                "error_type": "invalid_type",
            }

        if result and result.success:
            logger.info(
                "DOTMAC_ERP_ENTITY_SYNC_COMPLETE entity_type=%s entity_id=%s",
                entity_type,
                entity_id,
            )
        else:
            status = "error"
            logger.warning(
                "DOTMAC_ERP_ENTITY_SYNC_FAILED entity_type=%s entity_id=%s error_type=%s",
                entity_type,
                entity_id,
                result.error_type if result else None,
            )

        return {
            "success": bool(result.success if result else False),
            "entity_type": result.entity_type if result else entity_type,
            "entity_id": result.entity_id if result else entity_id,
            "error_type": result.error_type if result else "unknown",
            "status_code": result.status_code if result else None,
            "error": result.error if result else None,
        }

    except DotMacERPRateLimitError as e:
        status = "retry"
        retry_after = e.retry_after or 60
        logger.warning(
            "DOTMAC_ERP_ENTITY_SYNC_RATE_LIMITED entity_type=%s entity_id=%s retry_after=%s",
            entity_type,
            entity_id,
            retry_after,
        )
        raise self.retry(exc=e, countdown=retry_after)
    except DotMacERPTransientError as e:
        status = "retry"
        logger.warning(
            "DOTMAC_ERP_ENTITY_SYNC_RETRY entity_type=%s entity_id=%s error=%s",
            entity_type,
            entity_id,
            str(e),
        )
        raise
    except (DotMacERPAuthError, DotMacERPNotFoundError) as e:
        status = "error"
        logger.error(
            "DOTMAC_ERP_ENTITY_SYNC_NONRETRYABLE entity_type=%s entity_id=%s error=%s",
            entity_type,
            entity_id,
            str(e),
        )
        return {
            "success": False,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "error_type": "auth" if isinstance(e, DotMacERPAuthError) else "not_found",
            "status_code": e.status_code,
            "error": str(e),
        }
    except DotMacERPError as e:
        status = "error"
        logger.error(
            "DOTMAC_ERP_ENTITY_SYNC_ERROR entity_type=%s entity_id=%s error=%s",
            entity_type,
            entity_id,
            str(e),
        )
        return {
            "success": False,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "error_type": "error",
            "status_code": e.status_code,
            "error": str(e),
        }
    except Exception as e:
        status = "error"
        logger.exception(
            "DOTMAC_ERP_ENTITY_SYNC_FAILED entity_type=%s entity_id=%s error=%s",
            entity_type,
            entity_id,
            str(e),
        )
        session.rollback()
        raise

    finally:
        sync_service.close()
        session.close()
        duration = time.monotonic() - start
        observe_job("dotmac_erp_entity_sync", status, duration)


@celery_app.task(
    name="app.tasks.integrations.sync_dotmac_erp_shifts",
    time_limit=300,
    soft_time_limit=240,
)
def sync_dotmac_erp_shifts(days_ahead: int = 14, time_off_days_ahead: int = 30):
    """
    Sync technician shifts and time-off from DotMac ERP.

    Args:
        days_ahead: Days ahead to sync shifts
        time_off_days_ahead: Days ahead to sync time-off

    Returns:
        Dict with sync result summary
    """
    from app.models.domain_settings import SettingDomain
    from app.services import settings_spec
    from app.services.dotmac_erp import dotmac_erp_shift_sync, record_shift_sync_result

    def _coerce_int(value: object | None, default: int) -> int:
        if isinstance(value, bool):
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return default
        return default

    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger = get_logger(__name__)
    logger.info(
        "DOTMAC_ERP_SHIFT_SYNC_START days_ahead=%s time_off_days_ahead=%s",
        days_ahead,
        time_off_days_ahead,
    )

    try:
        settings_days_ahead = settings_spec.resolve_value(
            session, SettingDomain.integration, "dotmac_erp_shift_sync_days_ahead"
        )
        settings_time_off_days_ahead = settings_spec.resolve_value(
            session, SettingDomain.integration, "dotmac_erp_time_off_sync_days_ahead"
        )
        days_ahead = _coerce_int(settings_days_ahead, days_ahead)
        time_off_days_ahead = _coerce_int(settings_time_off_days_ahead, time_off_days_ahead)

        sync_service = dotmac_erp_shift_sync(session)
        result = sync_service.sync_all(
            days_ahead=days_ahead,
            time_off_days_ahead=time_off_days_ahead,
        )
        sync_service.close()

        logger.info(
            "DOTMAC_ERP_SHIFT_SYNC_COMPLETE shifts_created=%d shifts_updated=%d "
            "time_off_created=%d time_off_updated=%d technicians_matched=%d "
            "technicians_skipped=%d errors=%d duration=%.2fs",
            result.shifts_created,
            result.shifts_updated,
            result.time_off_created,
            result.time_off_updated,
            result.technicians_matched,
            result.technicians_skipped,
            len(result.errors),
            result.duration_seconds,
        )

        if result.has_errors:
            status = "partial"
            for error in result.errors:
                logger.warning("DOTMAC_ERP_SHIFT_SYNC_ERROR %s", error)

        # Record stats for dashboard
        record_shift_sync_result(
            shifts_created=result.shifts_created,
            shifts_updated=result.shifts_updated,
            time_off_created=result.time_off_created,
            time_off_updated=result.time_off_updated,
            technicians_matched=result.technicians_matched,
            technicians_skipped=result.technicians_skipped,
            errors=result.errors,
            duration_seconds=result.duration_seconds,
        )

        return {
            "shifts_created": result.shifts_created,
            "shifts_updated": result.shifts_updated,
            "time_off_created": result.time_off_created,
            "time_off_updated": result.time_off_updated,
            "technicians_matched": result.technicians_matched,
            "technicians_skipped": result.technicians_skipped,
            "total_synced": result.total_synced,
            "errors": result.errors,
            "duration_seconds": result.duration_seconds,
        }

    except Exception as e:
        status = "error"
        logger.exception("DOTMAC_ERP_SHIFT_SYNC_FAILED error=%s", str(e))
        session.rollback()
        raise

    finally:
        session.close()
        duration = time.monotonic() - start
        observe_job("dotmac_erp_shift_sync", status, duration)


@celery_app.task(
    name="app.tasks.integrations.sync_chatwoot",
    time_limit=3600,  # 1 hour for large imports
    soft_time_limit=3300,
)
def sync_chatwoot(
    max_conversations: int | None = 5000,
    skip_messages: bool = False,
):
    """
    Sync data from Chatwoot CRM.

    Args:
        max_conversations: Limit number of conversations (None for all)
        skip_messages: Skip importing messages for faster sync

    Returns:
        Dict with sync result summary
    """
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
        if isinstance(value, (int, str, bytes, bytearray)):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default
        return default

    try:
        # Get Chatwoot configuration from settings
        base_url = _coerce_str(
            settings_spec.resolve_value(
                session, SettingDomain.integration, "chatwoot_base_url"
            )
        )
        access_token = _coerce_str(
            settings_spec.resolve_value(
                session, SettingDomain.integration, "chatwoot_access_token"
            )
        )
        account_id = _coerce_int(
            settings_spec.resolve_value(
                session, SettingDomain.integration, "chatwoot_account_id"
            ),
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
            "conversations_created=%d conversations_updated=%d "
            "messages_created=%d errors=%d",
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

    except Exception as e:
        status = "error"
        logger.exception("CHATWOOT_SYNC_FAILED error=%s", str(e))
        session.rollback()
        raise

    finally:
        session.close()
        duration = time.monotonic() - start
        observe_job("chatwoot_sync", status, duration)

    # Unreachable, but keep for type checkers.
    return {"success": False}
