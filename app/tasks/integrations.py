import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

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


def _update_variation_erp_status(
    session: Session,
    project_uuid: object,
    new_status: str,
    logger: logging.Logger,
) -> None:
    """Update erp_sync_status on pending as-built variations linked to a project."""
    from app.models.vendor import AsBuiltRoute, InstallationProject

    try:
        pending_variations = (
            session.query(AsBuiltRoute)
            .join(InstallationProject, AsBuiltRoute.project_id == InstallationProject.id)
            .filter(InstallationProject.project_id == project_uuid)
            .filter(AsBuiltRoute.erp_sync_status == "pending")
            .all()
        )
        if not pending_variations:
            return
        now = datetime.now(UTC)
        for variation in pending_variations:
            variation.erp_sync_status = new_status
            variation.erp_sync_at = now
        session.commit()
        logger.info(
            "VARIATION_ERP_STATUS_UPDATED project_id=%s count=%d status=%s",
            project_uuid,
            len(pending_variations),
            new_status,
        )
    except Exception as exc:
        session.rollback()
        logger.warning("VARIATION_ERP_STATUS_UPDATE_FAILED project_id=%s error=%s", project_uuid, exc)


def _mark_project_variations_failed(
    session: Session, entity_type: str, entity_uuid: object, logger: logging.Logger
) -> None:
    if entity_type == "project":
        _update_variation_erp_status(session, entity_uuid, "failed", logger)


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
                _mark_project_variations_failed(session, entity_type, entity_uuid, logger)
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
            if entity_type == "project":
                _update_variation_erp_status(session, entity_uuid, "success", logger)
        else:
            status = "error"
            logger.warning(
                "DOTMAC_ERP_ENTITY_SYNC_FAILED entity_type=%s entity_id=%s error_type=%s",
                entity_type,
                entity_id,
                result.error_type if result else None,
            )
            if entity_type == "project":
                _update_variation_erp_status(session, entity_uuid, "failed", logger)

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
        _mark_project_variations_failed(session, entity_type, entity_uuid, logger)
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
        _mark_project_variations_failed(session, entity_type, entity_uuid, logger)
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
    name="app.tasks.integrations.sync_dotmac_erp_technicians",
    time_limit=300,
    soft_time_limit=240,
)
def sync_dotmac_erp_technicians():
    """Pull technicians from DotMac ERP employees feed.

    Rule: ERP employees in department "Projects" are treated as technicians.
    Behavior: Upsert TechnicianProfile records; deactivate ERP-linked technicians
    that are no longer eligible.
    """
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger = get_logger(__name__)
    logger.info("DOTMAC_ERP_TECHNICIAN_SYNC_START")

    try:
        from app.services.dotmac_erp import dotmac_erp_technician_sync

        # Guard against rare cases where a pooled connection is returned in a bad transaction
        # state (INTRANS), causing SQLAlchemy pre-ping to fail when toggling autocommit.
        try:
            session.execute(text("select 1"))
        except ProgrammingError as e:
            msg = str(e).lower()
            if "autocommit" in msg and "intrans" in msg:
                logger.warning("DOTMAC_ERP_TECHNICIAN_SYNC_DB_POOL_INTRANS resetting_pool=true error=%s", str(e))
                session.close()
                engine = getattr(SessionLocal, "kw", {}).get("bind")
                if engine is not None:
                    engine.dispose()
                session = SessionLocal()
                session.execute(text("select 1"))
            else:
                raise

        sync_service = dotmac_erp_technician_sync(session)
        result = sync_service.sync_all()
        sync_service.close()

        logger.info(
            "DOTMAC_ERP_TECHNICIAN_SYNC_COMPLETE persons_created=%d persons_updated=%d "
            "techs_created=%d techs_updated=%d techs_reactivated=%d techs_deactivated=%d "
            "employees_seen=%d employees_eligible=%d errors=%d duration=%.2fs",
            result.persons_created,
            result.persons_updated,
            result.technicians_created,
            result.technicians_updated,
            result.technicians_reactivated,
            result.technicians_deactivated,
            result.employees_seen,
            result.employees_eligible,
            len(result.errors),
            result.duration_seconds,
        )

        if result.has_errors:
            status = "partial"
            for error in result.errors[:10]:
                logger.warning("DOTMAC_ERP_TECHNICIAN_SYNC_ERROR %s", error)

        return {
            "persons_created": result.persons_created,
            "persons_updated": result.persons_updated,
            "technicians_created": result.technicians_created,
            "technicians_updated": result.technicians_updated,
            "technicians_reactivated": result.technicians_reactivated,
            "technicians_deactivated": result.technicians_deactivated,
            "employees_seen": result.employees_seen,
            "employees_eligible": result.employees_eligible,
            "total_synced": result.total_synced,
            "errors": result.errors,
            "duration_seconds": result.duration_seconds,
        }

    except Exception as e:
        status = "error"
        logger.exception("DOTMAC_ERP_TECHNICIAN_SYNC_FAILED error=%s", str(e))
        session.rollback()
        raise
    finally:
        session.close()
        duration = time.monotonic() - start
        observe_job("dotmac_erp_technician_sync", status, duration)


@celery_app.task(
    name="app.tasks.integrations.sync_material_request_to_erp",
    bind=True,
    time_limit=60,
    soft_time_limit=45,
    max_retries=5,
    autoretry_for=(DotMacERPTransientError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
def sync_material_request_to_erp(self, material_request_id: str):
    """Push an approved material request to DotMac ERP.

    Args:
        material_request_id: UUID of the material request to sync

    Returns:
        Dict with sync result
    """
    from app.services.dotmac_erp import (
        DotMacERPAuthError,
        DotMacERPError,
        DotMacERPRateLimitError,
        DotMacERPTransientError,
        record_material_request_sync_result,
    )
    from app.services.dotmac_erp.material_request_sync import dotmac_erp_material_request_sync

    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger = get_logger(__name__)
    logger.info("MATERIAL_REQUEST_SYNC_START material_request_id=%s", material_request_id)

    try:
        from sqlalchemy.orm import selectinload

        from app.models.material_request import MaterialRequest, MaterialRequestERPSyncStatus

        mr = session.get(
            MaterialRequest,
            coerce_uuid(material_request_id),
            options=[selectinload(MaterialRequest.items)],
        )
        if not mr:
            logger.warning("MATERIAL_REQUEST_SYNC_NOT_FOUND material_request_id=%s", material_request_id)
            return {"success": False, "error": "Material request not found"}

        mr.erp_sync_status = MaterialRequestERPSyncStatus.pending
        mr.erp_sync_error = None
        mr.erp_sync_attempts = (mr.erp_sync_attempts or 0) + 1
        session.commit()

        def record_result(success: bool, error: str | None = None, erp_id: str | None = None) -> None:
            record_material_request_sync_result(
                material_request_id=str(mr.id),
                erp_material_request_id=erp_id or mr.erp_material_request_id,
                success=success,
                error=error,
                duration_seconds=time.monotonic() - start,
            )

        sync_service = dotmac_erp_material_request_sync(session)
        try:
            result = sync_service.sync_material_request(mr)
        finally:
            sync_service.close()

        if result.success:
            mr.erp_sync_status = MaterialRequestERPSyncStatus.synced
            mr.erp_sync_error = None
            mr.erp_synced_at = datetime.now(UTC)
            if result.erp_material_request_id and not mr.erp_material_request_id:
                mr.erp_material_request_id = result.erp_material_request_id
            if result.erp_material_status:
                mr.erp_material_status = result.erp_material_status
            session.commit()
            record_result(True, erp_id=result.erp_material_request_id)
            logger.info(
                "MATERIAL_REQUEST_SYNC_COMPLETE material_request_id=%s erp_id=%s",
                material_request_id,
                result.erp_material_request_id,
            )
        else:
            status = "error"
            mr.erp_sync_status = MaterialRequestERPSyncStatus.failed
            mr.erp_sync_error = (result.error or "ERP sync failed")[:500]
            session.commit()
            record_result(False, error=result.error)
            logger.warning(
                "MATERIAL_REQUEST_SYNC_FAILED material_request_id=%s error=%s",
                material_request_id,
                result.error,
            )

        return {
            "success": result.success,
            "material_request_id": result.material_request_id,
            "erp_material_request_id": result.erp_material_request_id,
            "error": result.error,
        }

    except ValueError as e:
        status = "error"
        if "mr" in locals() and mr:
            from app.models.material_request import MaterialRequestERPSyncStatus

            mr.erp_sync_status = MaterialRequestERPSyncStatus.not_configured
            mr.erp_sync_error = str(e)[:500]
            session.commit()
            record_material_request_sync_result(
                material_request_id=str(mr.id),
                erp_material_request_id=mr.erp_material_request_id,
                success=False,
                error=str(e),
                duration_seconds=time.monotonic() - start,
            )
        logger.error("MATERIAL_REQUEST_SYNC_NOT_CONFIGURED material_request_id=%s error=%s", material_request_id, e)
        return {"success": False, "error": str(e), "error_type": "not_configured"}
    except DotMacERPRateLimitError as e:
        status = "retry"
        if "mr" in locals() and mr:
            from app.models.material_request import MaterialRequestERPSyncStatus

            mr.erp_sync_status = MaterialRequestERPSyncStatus.retrying
            mr.erp_sync_error = str(e)[:500]
            session.commit()
            record_material_request_sync_result(
                material_request_id=str(mr.id),
                erp_material_request_id=mr.erp_material_request_id,
                success=False,
                error=str(e),
                duration_seconds=time.monotonic() - start,
            )
        retry_after = e.retry_after or 60
        logger.warning(
            "MATERIAL_REQUEST_SYNC_RATE_LIMITED material_request_id=%s retry_after=%s",
            material_request_id,
            retry_after,
        )
        raise self.retry(exc=e, countdown=retry_after)
    except DotMacERPAuthError as e:
        status = "error"
        if "mr" in locals() and mr:
            from app.models.material_request import MaterialRequestERPSyncStatus

            mr.erp_sync_status = MaterialRequestERPSyncStatus.failed
            mr.erp_sync_error = str(e)[:500]
            session.commit()
            record_material_request_sync_result(
                material_request_id=str(mr.id),
                erp_material_request_id=mr.erp_material_request_id,
                success=False,
                error=str(e),
                duration_seconds=time.monotonic() - start,
            )
        logger.error(
            "MATERIAL_REQUEST_SYNC_AUTH_ERROR material_request_id=%s error=%s",
            material_request_id,
            str(e),
        )
        return {"success": False, "error": str(e), "error_type": "auth"}
    except DotMacERPTransientError as e:
        status = "retry"
        if "mr" in locals() and mr:
            from app.models.material_request import MaterialRequestERPSyncStatus

            mr.erp_sync_status = MaterialRequestERPSyncStatus.retrying
            mr.erp_sync_error = str(e)[:500]
            session.commit()
            record_material_request_sync_result(
                material_request_id=str(mr.id),
                erp_material_request_id=mr.erp_material_request_id,
                success=False,
                error=str(e),
                duration_seconds=time.monotonic() - start,
            )
        logger.warning(
            "MATERIAL_REQUEST_SYNC_TRANSIENT material_request_id=%s error=%s",
            material_request_id,
            str(e),
        )
        raise
    except DotMacERPError as e:
        status = "error"
        if "mr" in locals() and mr:
            from app.models.material_request import MaterialRequestERPSyncStatus

            mr.erp_sync_status = MaterialRequestERPSyncStatus.failed
            mr.erp_sync_error = str(e)[:500]
            session.commit()
            record_material_request_sync_result(
                material_request_id=str(mr.id),
                erp_material_request_id=mr.erp_material_request_id,
                success=False,
                error=str(e),
                duration_seconds=time.monotonic() - start,
            )
        logger.error(
            "MATERIAL_REQUEST_SYNC_ERROR material_request_id=%s error=%s",
            material_request_id,
            str(e),
        )
        return {"success": False, "error": str(e)}
    except Exception as e:
        status = "error"
        if "mr" in locals() and mr:
            try:
                from app.models.material_request import MaterialRequestERPSyncStatus

                session.rollback()
                mr.erp_sync_status = MaterialRequestERPSyncStatus.failed
                mr.erp_sync_error = str(e)[:500]
                session.commit()
                record_material_request_sync_result(
                    material_request_id=str(mr.id),
                    erp_material_request_id=mr.erp_material_request_id,
                    success=False,
                    error=str(e),
                    duration_seconds=time.monotonic() - start,
                )
            except Exception:
                session.rollback()
        logger.exception(
            "MATERIAL_REQUEST_SYNC_FAILED material_request_id=%s error=%s",
            material_request_id,
            str(e),
        )
        session.rollback()
        raise

    finally:
        session.close()
        duration = time.monotonic() - start
        observe_job("material_request_sync", status, duration)


@celery_app.task(
    name="app.tasks.integrations.refresh_material_request_erp_status",
    bind=True,
    time_limit=60,
    soft_time_limit=45,
    max_retries=5,
    autoretry_for=(DotMacERPTransientError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
def refresh_material_request_erp_status(self, material_request_id: str):
    """Refresh ERP-side material request stock/fulfillment status."""
    from app.services.dotmac_erp import (
        DotMacERPAuthError,
        DotMacERPError,
        DotMacERPRateLimitError,
        DotMacERPTransientError,
        record_material_request_sync_result,
    )
    from app.services.dotmac_erp.material_request_sync import dotmac_erp_material_request_sync

    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger = get_logger(__name__)
    logger.info("MATERIAL_REQUEST_ERP_STATUS_REFRESH_START material_request_id=%s", material_request_id)

    try:
        from sqlalchemy.orm import selectinload

        from app.models.material_request import MaterialRequest, MaterialRequestERPSyncStatus

        mr = session.get(
            MaterialRequest,
            coerce_uuid(material_request_id),
            options=[selectinload(MaterialRequest.items)],
        )
        if not mr:
            logger.warning("MATERIAL_REQUEST_ERP_STATUS_REFRESH_NOT_FOUND material_request_id=%s", material_request_id)
            return {"success": False, "error": "Material request not found"}

        mr.erp_sync_status = MaterialRequestERPSyncStatus.pending
        mr.erp_sync_error = None
        mr.erp_sync_attempts = (mr.erp_sync_attempts or 0) + 1
        session.commit()

        sync_service = dotmac_erp_material_request_sync(session)
        try:
            result = sync_service.refresh_material_request_status(mr)
        finally:
            sync_service.close()

        if result.success:
            mr.erp_sync_status = MaterialRequestERPSyncStatus.synced
            mr.erp_sync_error = None
            mr.erp_synced_at = datetime.now(UTC)
            if result.erp_material_status:
                mr.erp_material_status = result.erp_material_status
            session.commit()
            record_material_request_sync_result(
                material_request_id=str(mr.id),
                erp_material_request_id=result.erp_material_request_id or mr.erp_material_request_id,
                success=True,
                error=None,
                duration_seconds=time.monotonic() - start,
            )
            logger.info(
                "MATERIAL_REQUEST_ERP_STATUS_REFRESH_COMPLETE material_request_id=%s erp_status=%s",
                material_request_id,
                result.erp_material_status,
            )
        else:
            status = "error"
            mr.erp_sync_status = MaterialRequestERPSyncStatus.failed
            mr.erp_sync_error = (result.error or "ERP status refresh failed")[:500]
            session.commit()
            record_material_request_sync_result(
                material_request_id=str(mr.id),
                erp_material_request_id=mr.erp_material_request_id,
                success=False,
                error=result.error,
                duration_seconds=time.monotonic() - start,
            )

        return {
            "success": result.success,
            "material_request_id": result.material_request_id,
            "erp_material_request_id": result.erp_material_request_id,
            "erp_material_status": result.erp_material_status,
            "error": result.error,
        }

    except ValueError as e:
        status = "error"
        if "mr" in locals() and mr:
            from app.models.material_request import MaterialRequestERPSyncStatus

            mr.erp_sync_status = MaterialRequestERPSyncStatus.not_configured
            mr.erp_sync_error = str(e)[:500]
            session.commit()
        logger.error(
            "MATERIAL_REQUEST_ERP_STATUS_REFRESH_NOT_CONFIGURED material_request_id=%s error=%s",
            material_request_id,
            e,
        )
        return {"success": False, "error": str(e), "error_type": "not_configured"}
    except DotMacERPRateLimitError as e:
        status = "retry"
        if "mr" in locals() and mr:
            from app.models.material_request import MaterialRequestERPSyncStatus

            mr.erp_sync_status = MaterialRequestERPSyncStatus.retrying
            mr.erp_sync_error = str(e)[:500]
            session.commit()
        retry_after = e.retry_after or 60
        logger.warning(
            "MATERIAL_REQUEST_ERP_STATUS_REFRESH_RATE_LIMITED material_request_id=%s retry_after=%s",
            material_request_id,
            retry_after,
        )
        raise self.retry(exc=e, countdown=retry_after)
    except DotMacERPAuthError as e:
        status = "error"
        if "mr" in locals() and mr:
            from app.models.material_request import MaterialRequestERPSyncStatus

            mr.erp_sync_status = MaterialRequestERPSyncStatus.failed
            mr.erp_sync_error = str(e)[:500]
            session.commit()
        return {"success": False, "error": str(e), "error_type": "auth"}
    except DotMacERPTransientError as e:
        status = "retry"
        if "mr" in locals() and mr:
            from app.models.material_request import MaterialRequestERPSyncStatus

            mr.erp_sync_status = MaterialRequestERPSyncStatus.retrying
            mr.erp_sync_error = str(e)[:500]
            session.commit()
        logger.warning(
            "MATERIAL_REQUEST_ERP_STATUS_REFRESH_TRANSIENT material_request_id=%s error=%s",
            material_request_id,
            str(e),
        )
        raise
    except DotMacERPError as e:
        status = "error"
        if "mr" in locals() and mr:
            from app.models.material_request import MaterialRequestERPSyncStatus

            mr.erp_sync_status = MaterialRequestERPSyncStatus.failed
            mr.erp_sync_error = str(e)[:500]
            session.commit()
        return {"success": False, "error": str(e)}
    except Exception as e:
        status = "error"
        if "mr" in locals() and mr:
            try:
                from app.models.material_request import MaterialRequestERPSyncStatus

                session.rollback()
                mr.erp_sync_status = MaterialRequestERPSyncStatus.failed
                mr.erp_sync_error = str(e)[:500]
                session.commit()
            except Exception:
                session.rollback()
        logger.exception(
            "MATERIAL_REQUEST_ERP_STATUS_REFRESH_FAILED material_request_id=%s error=%s",
            material_request_id,
            str(e),
        )
        raise
    finally:
        session.close()
        duration = time.monotonic() - start
        observe_job("material_request_erp_status_refresh", status, duration)


@celery_app.task(
    name="app.tasks.integrations.refresh_pending_material_request_erp_statuses",
    time_limit=300,
    soft_time_limit=240,
)
def refresh_pending_material_request_erp_statuses(limit: int = 50):
    """Refresh ERP status for issued material requests still awaiting ERP-side completion."""
    from app.models.material_request import MaterialRequest, MaterialRequestERPSyncStatus, MaterialRequestStatus
    from app.services.dotmac_erp import DotMacERPError, DotMacERPTransientError, record_material_request_sync_result
    from app.services.dotmac_erp.material_request_sync import dotmac_erp_material_request_sync

    start = time.monotonic()
    status = "success"
    refreshed = 0
    failed = 0
    skipped = 0
    errors: list[str] = []
    session = SessionLocal()
    logger = get_logger(__name__)
    logger.info("PENDING_MATERIAL_REQUEST_ERP_STATUS_REFRESH_START limit=%s", limit)

    try:
        batch_limit = max(min(int(limit or 50), 200), 1)
        candidates = (
            session.query(MaterialRequest)
            .filter(MaterialRequest.status.in_([MaterialRequestStatus.issued, MaterialRequestStatus.approved]))
            .filter(
                or_(
                    MaterialRequest.erp_material_status == "pending_stock",
                    MaterialRequest.erp_material_request_id.isnot(None),
                )
            )
            .filter(
                or_(
                    MaterialRequest.erp_material_status.is_(None),
                    MaterialRequest.erp_material_status.notin_(["fulfilled", "complete", "completed", "canceled"]),
                )
            )
            .order_by(MaterialRequest.created_at.asc())
            .limit(batch_limit)
            .all()
        )

        if not candidates:
            logger.info("PENDING_MATERIAL_REQUEST_ERP_STATUS_REFRESH_EMPTY")
            return {"success": True, "refreshed": 0, "failed": 0, "skipped": 0, "errors": []}

        try:
            sync_service = dotmac_erp_material_request_sync(session)
        except ValueError as exc:
            status = "error"
            message = str(exc)
            for mr in candidates:
                mr.erp_sync_status = MaterialRequestERPSyncStatus.not_configured
                mr.erp_sync_error = message[:500]
                failed += 1
            session.commit()
            return {"success": False, "refreshed": 0, "failed": failed, "skipped": 0, "errors": [message]}

        try:
            for mr in candidates:
                item_start = time.monotonic()
                mr.erp_sync_status = MaterialRequestERPSyncStatus.pending
                mr.erp_sync_error = None
                mr.erp_sync_attempts = (mr.erp_sync_attempts or 0) + 1
                session.commit()

                try:
                    result = sync_service.refresh_material_request_status(mr)
                    if result.success:
                        mr.erp_sync_status = MaterialRequestERPSyncStatus.synced
                        mr.erp_sync_error = None
                        mr.erp_synced_at = datetime.now(UTC)
                        if result.erp_material_status:
                            mr.erp_material_status = result.erp_material_status
                        session.commit()
                        record_material_request_sync_result(
                            material_request_id=str(mr.id),
                            erp_material_request_id=result.erp_material_request_id or mr.erp_material_request_id,
                            success=True,
                            error=None,
                            duration_seconds=time.monotonic() - item_start,
                        )
                        refreshed += 1
                    else:
                        mr.erp_sync_status = MaterialRequestERPSyncStatus.failed
                        mr.erp_sync_error = (result.error or "ERP status refresh failed")[:500]
                        session.commit()
                        record_material_request_sync_result(
                            material_request_id=str(mr.id),
                            erp_material_request_id=mr.erp_material_request_id,
                            success=False,
                            error=result.error,
                            duration_seconds=time.monotonic() - item_start,
                        )
                        failed += 1
                        if result.error:
                            errors.append(result.error)
                except DotMacERPTransientError as exc:
                    mr.erp_sync_status = MaterialRequestERPSyncStatus.retrying
                    mr.erp_sync_error = str(exc)[:500]
                    session.commit()
                    failed += 1
                    errors.append(str(exc))
                except DotMacERPError as exc:
                    mr.erp_sync_status = MaterialRequestERPSyncStatus.failed
                    mr.erp_sync_error = str(exc)[:500]
                    session.commit()
                    failed += 1
                    errors.append(str(exc))
                except Exception as exc:
                    session.rollback()
                    refreshed_mr = session.get(MaterialRequest, mr.id)
                    if refreshed_mr:
                        refreshed_mr.erp_sync_status = MaterialRequestERPSyncStatus.failed
                        refreshed_mr.erp_sync_error = str(exc)[:500]
                        session.commit()
                    failed += 1
                    errors.append(str(exc))
        finally:
            sync_service.close()

        if failed:
            status = "partial" if refreshed else "error"
        return {
            "success": failed == 0,
            "refreshed": refreshed,
            "failed": failed,
            "skipped": skipped,
            "errors": errors[:20],
        }
    except Exception:
        status = "error"
        session.rollback()
        raise
    finally:
        session.close()
        duration = time.monotonic() - start
        observe_job("pending_material_request_erp_status_refresh", status, duration)


@celery_app.task(
    name="app.tasks.integrations.sync_expense_request_to_erp",
    bind=True,
    time_limit=60,
    soft_time_limit=45,
    max_retries=5,
    autoretry_for=(DotMacERPTransientError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
def sync_expense_request_to_erp(self, expense_request_id: str):
    """Push a submitted field expense request to DotMac ERP as an expense claim.

    Args:
        expense_request_id: UUID of the expense request to sync

    Returns:
        Dict with sync result
    """
    from app.services.dotmac_erp import (
        DotMacERPAuthError,
        DotMacERPError,
        DotMacERPRateLimitError,
        DotMacERPTransientError,
    )
    from app.services.dotmac_erp.expense_request_sync import dotmac_erp_expense_request_sync

    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger = get_logger(__name__)
    logger.info("EXPENSE_REQUEST_SYNC_START expense_request_id=%s", expense_request_id)

    try:
        from sqlalchemy.orm import selectinload

        from app.models.expense_request import ExpenseRequest, ExpenseRequestERPSyncStatus

        er = session.get(
            ExpenseRequest,
            coerce_uuid(expense_request_id),
            options=[selectinload(ExpenseRequest.items)],
        )
        if not er:
            logger.warning("EXPENSE_REQUEST_SYNC_NOT_FOUND expense_request_id=%s", expense_request_id)
            return {"success": False, "error": "Expense request not found"}

        er.erp_sync_status = ExpenseRequestERPSyncStatus.pending
        er.erp_sync_error = None
        er.erp_sync_attempts = (er.erp_sync_attempts or 0) + 1
        session.commit()

        sync_service = dotmac_erp_expense_request_sync(session)
        try:
            result = sync_service.sync_expense_request(er)
        finally:
            sync_service.close()

        if result.success:
            er.erp_sync_status = ExpenseRequestERPSyncStatus.synced
            er.erp_sync_error = None
            er.erp_synced_at = datetime.now(UTC)
            session.commit()
            logger.info(
                "EXPENSE_REQUEST_SYNC_COMPLETE expense_request_id=%s erp_claim_id=%s",
                expense_request_id,
                result.erp_expense_claim_id,
            )
        else:
            status = "error"
            er.erp_sync_status = ExpenseRequestERPSyncStatus.failed
            er.erp_sync_error = (result.error or "ERP sync failed")[:500]
            session.commit()
            logger.warning(
                "EXPENSE_REQUEST_SYNC_FAILED expense_request_id=%s error=%s",
                expense_request_id,
                result.error,
            )

        return {
            "success": result.success,
            "expense_request_id": result.expense_request_id,
            "erp_expense_claim_id": result.erp_expense_claim_id,
            "error": result.error,
        }

    except ValueError as e:
        status = "error"
        if "er" in locals() and er:
            from app.models.expense_request import ExpenseRequestERPSyncStatus

            er.erp_sync_status = ExpenseRequestERPSyncStatus.not_configured
            er.erp_sync_error = str(e)[:500]
            session.commit()
        logger.error("EXPENSE_REQUEST_SYNC_NOT_CONFIGURED expense_request_id=%s error=%s", expense_request_id, e)
        return {"success": False, "error": str(e), "error_type": "not_configured"}
    except DotMacERPRateLimitError as e:
        status = "retry"
        if "er" in locals() and er:
            from app.models.expense_request import ExpenseRequestERPSyncStatus

            er.erp_sync_status = ExpenseRequestERPSyncStatus.retrying
            er.erp_sync_error = str(e)[:500]
            session.commit()
        retry_after = e.retry_after or 60
        logger.warning(
            "EXPENSE_REQUEST_SYNC_RATE_LIMITED expense_request_id=%s retry_after=%s",
            expense_request_id,
            retry_after,
        )
        raise self.retry(exc=e, countdown=retry_after)
    except DotMacERPAuthError as e:
        status = "error"
        if "er" in locals() and er:
            from app.models.expense_request import ExpenseRequestERPSyncStatus

            er.erp_sync_status = ExpenseRequestERPSyncStatus.failed
            er.erp_sync_error = str(e)[:500]
            session.commit()
        logger.error(
            "EXPENSE_REQUEST_SYNC_AUTH_ERROR expense_request_id=%s error=%s",
            expense_request_id,
            str(e),
        )
        return {"success": False, "error": str(e), "error_type": "auth"}
    except DotMacERPTransientError as e:
        status = "retry"
        if "er" in locals() and er:
            from app.models.expense_request import ExpenseRequestERPSyncStatus

            er.erp_sync_status = ExpenseRequestERPSyncStatus.retrying
            er.erp_sync_error = str(e)[:500]
            session.commit()
        logger.warning(
            "EXPENSE_REQUEST_SYNC_TRANSIENT expense_request_id=%s error=%s",
            expense_request_id,
            str(e),
        )
        raise
    except DotMacERPError as e:
        status = "error"
        if "er" in locals() and er:
            from app.models.expense_request import ExpenseRequestERPSyncStatus

            er.erp_sync_status = ExpenseRequestERPSyncStatus.failed
            er.erp_sync_error = str(e)[:500]
            session.commit()
        logger.error(
            "EXPENSE_REQUEST_SYNC_ERROR expense_request_id=%s error=%s",
            expense_request_id,
            str(e),
        )
        return {"success": False, "error": str(e)}
    except Exception as e:
        status = "error"
        if "er" in locals() and er:
            try:
                from app.models.expense_request import ExpenseRequestERPSyncStatus

                session.rollback()
                er.erp_sync_status = ExpenseRequestERPSyncStatus.failed
                er.erp_sync_error = str(e)[:500]
                session.commit()
            except Exception:
                session.rollback()
        logger.exception(
            "EXPENSE_REQUEST_SYNC_FAILED expense_request_id=%s error=%s",
            expense_request_id,
            str(e),
        )
        session.rollback()
        raise

    finally:
        session.close()
        duration = time.monotonic() - start
        observe_job("expense_request_sync", status, duration)


@celery_app.task(
    name="app.tasks.integrations.refresh_expense_request_erp_status",
    bind=True,
    time_limit=60,
    soft_time_limit=45,
    max_retries=3,
    autoretry_for=(DotMacERPTransientError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
def refresh_expense_request_erp_status(self, expense_request_id: str):
    """Pull the latest ERP claim status for one expense request."""
    from app.services.dotmac_erp import DotMacERPError
    from app.services.dotmac_erp.expense_request_sync import dotmac_erp_expense_request_sync

    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger = get_logger(__name__)
    logger.info("EXPENSE_REQUEST_ERP_STATUS_REFRESH_START expense_request_id=%s", expense_request_id)

    try:
        from sqlalchemy.orm import selectinload

        from app.models.expense_request import ExpenseRequest

        er = session.get(
            ExpenseRequest,
            coerce_uuid(expense_request_id),
            options=[selectinload(ExpenseRequest.items)],
        )
        if not er:
            return {"success": False, "error": "Expense request not found"}
        if not er.erp_expense_claim_id:
            return {"success": False, "error": "Expense request has not been synced to ERP yet"}

        sync_service = dotmac_erp_expense_request_sync(session)
        try:
            result = sync_service.refresh_expense_request_status(er)
        finally:
            sync_service.close()

        if not result.success:
            status = "error"
        logger.info(
            "EXPENSE_REQUEST_ERP_STATUS_REFRESH_COMPLETE expense_request_id=%s claim_status=%s",
            expense_request_id,
            result.erp_claim_status,
        )
        return {
            "success": result.success,
            "expense_request_id": result.expense_request_id,
            "erp_claim_status": result.erp_claim_status,
            "error": result.error,
        }
    except ValueError as e:
        status = "error"
        logger.warning(
            "EXPENSE_REQUEST_ERP_STATUS_REFRESH_NOT_CONFIGURED expense_request_id=%s error=%s",
            expense_request_id,
            e,
        )
        return {"success": False, "error": str(e), "error_type": "not_configured"}
    except DotMacERPTransientError:
        status = "retry"
        raise
    except DotMacERPError as e:
        status = "error"
        logger.error(
            "EXPENSE_REQUEST_ERP_STATUS_REFRESH_ERROR expense_request_id=%s error=%s",
            expense_request_id,
            str(e),
        )
        return {"success": False, "error": str(e)}
    except Exception:
        status = "error"
        session.rollback()
        raise
    finally:
        session.close()
        duration = time.monotonic() - start
        observe_job("expense_request_erp_status_refresh", status, duration)


@celery_app.task(
    name="app.tasks.integrations.refresh_pending_expense_request_erp_statuses",
    time_limit=300,
    soft_time_limit=240,
)
def refresh_pending_expense_request_erp_statuses(batch_limit: int = 100):
    """Poll ERP for claim status on expense requests awaiting approval/payment."""
    from app.services.dotmac_erp import DotMacERPError
    from app.services.dotmac_erp.expense_request_sync import dotmac_erp_expense_request_sync

    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger = get_logger(__name__)
    errors: list[str] = []
    results: dict[str, object] = {"processed": 0, "updated": 0, "errors": errors}
    processed = 0
    updated = 0

    try:
        from sqlalchemy.orm import selectinload

        from app.models.expense_request import ExpenseRequest, ExpenseRequestStatus

        batch_limit = max(1, min(int(batch_limit or 100), 200))
        pending = (
            session.query(ExpenseRequest)
            .options(selectinload(ExpenseRequest.items))
            .filter(ExpenseRequest.is_active.is_(True))
            .filter(ExpenseRequest.erp_expense_claim_id.isnot(None))
            .filter(ExpenseRequest.status.in_([ExpenseRequestStatus.submitted, ExpenseRequestStatus.approved]))
            .order_by(ExpenseRequest.updated_at.asc())
            .limit(batch_limit)
            .all()
        )
        if not pending:
            return results

        try:
            sync_service = dotmac_erp_expense_request_sync(session)
        except ValueError:
            logger.info("EXPENSE_REQUEST_ERP_STATUS_REFRESH_SKIPPED reason=not_configured")
            return results

        try:
            for er in pending:
                processed += 1
                try:
                    result = sync_service.refresh_expense_request_status(er)
                    if result.success:
                        updated += 1
                    elif result.error_type != "NotFound":
                        errors.append(f"{er.id}: {result.error}")
                except DotMacERPError as exc:
                    session.rollback()
                    errors.append(f"{er.id}: {exc}")
        finally:
            sync_service.close()

        results["processed"] = processed
        results["updated"] = updated
        logger.info(
            "PENDING_EXPENSE_REQUEST_ERP_STATUS_REFRESH_COMPLETE processed=%s updated=%s errors=%s",
            processed,
            updated,
            len(errors),
        )
        return results
    except Exception:
        status = "error"
        session.rollback()
        raise
    finally:
        session.close()
        duration = time.monotonic() - start
        observe_job("pending_expense_request_erp_status_refresh", status, duration)


@celery_app.task(
    name="app.tasks.integrations.redrive_failed_erp_pushes",
    time_limit=300,
    soft_time_limit=240,
)
def redrive_failed_erp_pushes():
    """Re-drive ERP money pushes stuck in failed or stale in-flight states.

    Thin wrapper; the sweep logic (and its config knobs) lives in
    app.services.dotmac_erp.push_redrive.redrive_failed_erp_pushes.
    """
    from app.services.dotmac_erp import push_redrive

    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    try:
        return push_redrive.redrive_failed_erp_pushes(session)
    except Exception:
        status = "error"
        session.rollback()
        raise
    finally:
        session.close()
        duration = time.monotonic() - start
        observe_job("erp_push_redrive", status, duration)


@celery_app.task(
    name="app.tasks.integrations.sync_purchase_order_to_erp",
    bind=True,
    time_limit=60,
    soft_time_limit=45,
    max_retries=5,
    autoretry_for=(DotMacERPTransientError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
def sync_purchase_order_to_erp(self, work_order_id: str, quote_id: str):
    """Push a purchase order to DotMac ERP for an approved vendor quote.

    Args:
        work_order_id: UUID of the work order
        quote_id: UUID of the approved ProjectQuote

    Returns:
        Dict with sync result
    """
    from sqlalchemy.orm import joinedload, selectinload

    from app.models.vendor import ProjectQuote
    from app.models.workforce import WorkOrder
    from app.services.dotmac_erp import (
        DotMacERPAuthError,
        DotMacERPError,
        DotMacERPRateLimitError,
        DotMacERPTransientError,
    )
    from app.services.dotmac_erp.po_sync import dotmac_erp_purchase_order_sync
    from app.services.dotmac_erp.push_redrive import (
        ERP_SYNC_FAILED,
        ERP_SYNC_NOT_CONFIGURED,
        ERP_SYNC_PENDING,
        ERP_SYNC_RETRYING,
        ERP_SYNC_SYNCED,
    )

    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger = get_logger(__name__)
    logger.info("PO_SYNC_START work_order_id=%s quote_id=%s", work_order_id, quote_id)

    try:
        wo = session.get(WorkOrder, coerce_uuid(work_order_id))
        if not wo:
            logger.warning("PO_SYNC_WO_NOT_FOUND work_order_id=%s", work_order_id)
            return {"success": False, "error": "Work order not found"}

        quote = session.get(
            ProjectQuote,
            coerce_uuid(quote_id),
            options=[
                selectinload(ProjectQuote.line_items),
                joinedload(ProjectQuote.vendor),
                joinedload(ProjectQuote.reviewed_by),
            ],
        )
        if not quote:
            logger.warning("PO_SYNC_QUOTE_NOT_FOUND quote_id=%s", quote_id)
            return {"success": False, "error": "Quote not found"}

        wo.erp_sync_status = ERP_SYNC_PENDING
        wo.erp_sync_error = None
        if wo.erp_po_quote_id != quote.id:
            wo.erp_po_quote_id = quote.id
        session.commit()

        sync_service = dotmac_erp_purchase_order_sync(session)
        result = sync_service.sync_purchase_order(wo, quote)
        sync_service.close()

        if result.success:
            wo.erp_sync_status = ERP_SYNC_SYNCED
            wo.erp_sync_error = None
            wo.erp_synced_at = datetime.now(UTC)
            session.commit()
            logger.info(
                "PO_SYNC_COMPLETE work_order_id=%s erp_po_id=%s",
                work_order_id,
                result.erp_po_id,
            )
        else:
            status = "error"
            wo.erp_sync_status = ERP_SYNC_FAILED
            wo.erp_sync_error = (result.error or "ERP purchase order sync failed")[:500]
            session.commit()
            logger.warning(
                "PO_SYNC_FAILED work_order_id=%s error=%s",
                work_order_id,
                result.error,
            )

        return {
            "success": result.success,
            "work_order_id": result.work_order_id,
            "erp_po_id": result.erp_po_id,
            "error": result.error,
        }

    except ValueError as e:
        status = "error"
        if "wo" in locals() and wo:
            wo.erp_sync_status = ERP_SYNC_NOT_CONFIGURED
            wo.erp_sync_error = str(e)[:500]
            session.commit()
        logger.error("PO_SYNC_NOT_CONFIGURED work_order_id=%s error=%s", work_order_id, e)
        return {"success": False, "error": str(e), "error_type": "not_configured"}
    except DotMacERPRateLimitError as e:
        status = "retry"
        if "wo" in locals() and wo:
            wo.erp_sync_status = ERP_SYNC_RETRYING
            wo.erp_sync_error = str(e)[:500]
            session.commit()
        retry_after = e.retry_after or 60
        logger.warning(
            "PO_SYNC_RATE_LIMITED work_order_id=%s retry_after=%s",
            work_order_id,
            retry_after,
        )
        raise self.retry(exc=e, countdown=retry_after)
    except DotMacERPAuthError as e:
        status = "error"
        if "wo" in locals() and wo:
            wo.erp_sync_status = ERP_SYNC_FAILED
            wo.erp_sync_error = str(e)[:500]
            session.commit()
        logger.error(
            "PO_SYNC_AUTH_ERROR work_order_id=%s error=%s",
            work_order_id,
            str(e),
        )
        return {"success": False, "error": str(e), "error_type": "auth"}
    except DotMacERPTransientError as e:
        status = "retry"
        if "wo" in locals() and wo:
            wo.erp_sync_status = ERP_SYNC_RETRYING
            wo.erp_sync_error = str(e)[:500]
            session.commit()
        logger.warning(
            "PO_SYNC_TRANSIENT work_order_id=%s error=%s",
            work_order_id,
            str(e),
        )
        raise
    except DotMacERPError as e:
        status = "error"
        if "wo" in locals() and wo:
            wo.erp_sync_status = ERP_SYNC_FAILED
            wo.erp_sync_error = str(e)[:500]
            session.commit()
        logger.error(
            "PO_SYNC_ERROR work_order_id=%s error=%s",
            work_order_id,
            str(e),
        )
        return {"success": False, "error": str(e)}
    except Exception as e:
        status = "error"
        session.rollback()
        if "wo" in locals() and wo:
            try:
                wo.erp_sync_status = ERP_SYNC_FAILED
                wo.erp_sync_error = str(e)[:500]
                session.commit()
            except Exception:
                session.rollback()
        logger.exception(
            "PO_SYNC_FAILED work_order_id=%s error=%s",
            work_order_id,
            str(e),
        )
        raise

    finally:
        session.close()
        duration = time.monotonic() - start
        observe_job("po_sync", status, duration)


@celery_app.task(
    name="app.tasks.integrations.sync_purchase_invoice_to_erp",
    bind=True,
    time_limit=60,
    soft_time_limit=45,
    max_retries=5,
    autoretry_for=(DotMacERPTransientError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
def sync_purchase_invoice_to_erp(self, invoice_id: str):
    """Push a purchase invoice to DotMac ERP after approval."""
    from sqlalchemy.orm import joinedload, selectinload

    from app.models.vendor import InstallationProject, VendorPurchaseInvoice
    from app.services.dotmac_erp import (
        DotMacERPAuthError,
        DotMacERPError,
        DotMacERPRateLimitError,
        DotMacERPTransientError,
        dotmac_erp_purchase_invoice_sync,
    )
    from app.services.dotmac_erp.push_redrive import (
        ERP_SYNC_FAILED,
        ERP_SYNC_NOT_CONFIGURED,
        ERP_SYNC_PENDING,
        ERP_SYNC_RETRYING,
        ERP_SYNC_SYNCED,
    )

    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger = get_logger(__name__)
    logger.info("PURCHASE_INVOICE_SYNC_START invoice_id=%s", invoice_id)

    try:
        invoice = session.get(
            VendorPurchaseInvoice,
            coerce_uuid(invoice_id),
            options=[
                selectinload(VendorPurchaseInvoice.line_items),
                joinedload(VendorPurchaseInvoice.vendor),
                joinedload(VendorPurchaseInvoice.reviewed_by),
                joinedload(VendorPurchaseInvoice.project).joinedload(InstallationProject.project),
            ],
        )
        if not invoice:
            logger.warning("PURCHASE_INVOICE_SYNC_NOT_FOUND invoice_id=%s", invoice_id)
            return {"success": False, "error": "Purchase invoice not found"}

        invoice.erp_sync_status = ERP_SYNC_PENDING
        invoice.erp_sync_error = None
        session.commit()

        sync_service = dotmac_erp_purchase_invoice_sync(session)
        result = sync_service.sync_purchase_invoice(invoice)
        sync_service.close()

        if result.success:
            invoice.erp_sync_status = ERP_SYNC_SYNCED
            invoice.erp_sync_error = None
            if invoice.erp_synced_at is None:
                invoice.erp_synced_at = datetime.now(UTC)
            session.commit()
            logger.info(
                "PURCHASE_INVOICE_SYNC_COMPLETE invoice_id=%s erp_purchase_invoice_id=%s",
                invoice_id,
                result.erp_purchase_invoice_id,
            )
        else:
            status = "error"
            if result.error_type == "PendingPrerequisite":
                # Not terminal: the PO hasn't landed in ERP yet. Stay "pending"
                # so the re-drive sweep retries once it goes stale.
                invoice.erp_sync_status = ERP_SYNC_PENDING
            else:
                invoice.erp_sync_status = ERP_SYNC_FAILED
            invoice.erp_sync_error = (result.error or "ERP purchase invoice sync failed")[:500]
            session.commit()
            logger.warning(
                "PURCHASE_INVOICE_SYNC_FAILED invoice_id=%s error=%s",
                invoice_id,
                result.error,
            )

        return {
            "success": result.success,
            "invoice_id": result.invoice_id,
            "erp_purchase_invoice_id": result.erp_purchase_invoice_id,
            "error": result.error,
        }
    except ValueError as e:
        status = "error"
        if "invoice" in locals() and invoice:
            invoice.erp_sync_status = ERP_SYNC_NOT_CONFIGURED
            invoice.erp_sync_error = str(e)[:500]
            session.commit()
        logger.error("PURCHASE_INVOICE_SYNC_NOT_CONFIGURED invoice_id=%s error=%s", invoice_id, e)
        return {"success": False, "error": str(e), "error_type": "not_configured"}
    except DotMacERPRateLimitError as e:
        status = "retry"
        if "invoice" in locals() and invoice:
            invoice.erp_sync_status = ERP_SYNC_RETRYING
            invoice.erp_sync_error = str(e)[:500]
            session.commit()
        retry_after = e.retry_after or 60
        logger.warning(
            "PURCHASE_INVOICE_SYNC_RATE_LIMITED invoice_id=%s retry_after=%s",
            invoice_id,
            retry_after,
        )
        raise self.retry(exc=e, countdown=retry_after)
    except DotMacERPAuthError as e:
        status = "error"
        if "invoice" in locals() and invoice:
            invoice.erp_sync_status = ERP_SYNC_FAILED
            invoice.erp_sync_error = str(e)[:500]
            session.commit()
        logger.error("PURCHASE_INVOICE_SYNC_AUTH_ERROR invoice_id=%s error=%s", invoice_id, str(e))
        return {"success": False, "error": str(e), "error_type": "auth"}
    except DotMacERPTransientError as e:
        status = "retry"
        if "invoice" in locals() and invoice:
            invoice.erp_sync_status = ERP_SYNC_RETRYING
            invoice.erp_sync_error = str(e)[:500]
            session.commit()
        logger.warning("PURCHASE_INVOICE_SYNC_TRANSIENT invoice_id=%s error=%s", invoice_id, str(e))
        raise
    except DotMacERPError as e:
        status = "error"
        if "invoice" in locals() and invoice:
            invoice.erp_sync_status = ERP_SYNC_FAILED
            invoice.erp_sync_error = str(e)[:500]
            session.commit()
        logger.error("PURCHASE_INVOICE_SYNC_ERROR invoice_id=%s error=%s", invoice_id, str(e))
        return {"success": False, "error": str(e)}
    except Exception as e:
        status = "error"
        session.rollback()
        if "invoice" in locals() and invoice:
            try:
                invoice.erp_sync_status = ERP_SYNC_FAILED
                invoice.erp_sync_error = str(e)[:500]
                session.commit()
            except Exception:
                session.rollback()
        logger.exception("PURCHASE_INVOICE_SYNC_FAILED invoice_id=%s error=%s", invoice_id, str(e))
        raise
    finally:
        session.close()
        duration = time.monotonic() - start
        observe_job("purchase_invoice_sync", status, duration)


@celery_app.task(
    name="app.tasks.integrations.sync_dotmac_erp_contacts",
    time_limit=600,
    soft_time_limit=540,
)
def sync_dotmac_erp_contacts():
    """Pull customers and contacts from DotMac ERP.

    Syncs organizations (companies) and persons (contacts).

    Returns:
        Dict with sync result summary
    """
    from app.services.dotmac_erp import dotmac_erp_contact_sync, record_contact_sync_result

    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger = get_logger(__name__)
    logger.info("DOTMAC_ERP_CONTACT_SYNC_START")

    try:
        sync_service = dotmac_erp_contact_sync(session)
        result = sync_service.sync_all()
        sync_service.close()

        logger.info(
            "DOTMAC_ERP_CONTACT_SYNC_COMPLETE orgs_created=%d orgs_updated=%d "
            "contacts_created=%d contacts_updated=%d contacts_linked=%d "
            "channels_upserted=%d errors=%d duration=%.2fs",
            result.orgs_created,
            result.orgs_updated,
            result.contacts_created,
            result.contacts_updated,
            result.contacts_linked,
            result.channels_upserted,
            len(result.errors),
            result.duration_seconds,
        )

        if result.has_errors:
            status = "partial"
            for error in result.errors[:10]:
                logger.warning("DOTMAC_ERP_CONTACT_SYNC_ERROR %s", error)

        record_contact_sync_result(
            orgs_created=result.orgs_created,
            orgs_updated=result.orgs_updated,
            contacts_created=result.contacts_created,
            contacts_updated=result.contacts_updated,
            contacts_linked=result.contacts_linked,
            channels_upserted=result.channels_upserted,
            errors=result.errors,
            duration_seconds=result.duration_seconds,
        )

        return {
            "orgs_created": result.orgs_created,
            "orgs_updated": result.orgs_updated,
            "contacts_created": result.contacts_created,
            "contacts_updated": result.contacts_updated,
            "contacts_linked": result.contacts_linked,
            "channels_upserted": result.channels_upserted,
            "total_synced": result.total_synced,
            "errors": result.errors,
            "duration_seconds": result.duration_seconds,
        }

    except Exception as e:
        status = "error"
        logger.exception("DOTMAC_ERP_CONTACT_SYNC_FAILED error=%s", str(e))
        session.rollback()
        raise

    finally:
        session.close()
        duration = time.monotonic() - start
        observe_job("dotmac_erp_contact_sync", status, duration)


@celery_app.task(
    name="app.tasks.integrations.detect_dotmac_erp_identity_drift",
    time_limit=600,
    soft_time_limit=540,
)
def detect_dotmac_erp_identity_drift():
    """Detect CRM <-> ERP customer/contact identity drift.

    Read-only toward CRM business rows and ERP. Findings are emitted through the
    existing infrastructure-alert lifecycle.
    """
    from app.services.dotmac_erp.identity_drift import run_identity_drift_detection
    from app.services.infrastructure_health import upsert_alerts_from_results

    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger = get_logger(__name__)
    logger.info("DOTMAC_ERP_IDENTITY_DRIFT_START")

    try:
        run = run_identity_drift_detection(session)
        alert_stats = upsert_alerts_from_results(session, run.results)
        material = sum(
            count
            for check_key, count in run.counts_by_check.items()
            if check_key
            in {
                "dotmac_erp_identity_erp_duplicate_company_id",
                "dotmac_erp_identity_erp_duplicate_contact_id",
                "dotmac_erp_identity_crm_duplicate_org_erp_id",
                "dotmac_erp_identity_crm_duplicate_person_erp_person_id",
                "dotmac_erp_identity_crm_duplicate_person_erp_customer_id",
                "dotmac_erp_identity_crm_person_erp_company_mismatch",
            }
        )
        if run.unhealthy:
            status = "partial"
            logger.warning(
                "DOTMAC_ERP_IDENTITY_DRIFT findings=%s material=%s counts=%s alerts=%s duration=%.2fs",
                run.unhealthy,
                material,
                run.counts_by_check,
                alert_stats,
                run.duration_seconds,
            )
        else:
            logger.info(
                "DOTMAC_ERP_IDENTITY_DRIFT_CLEAN counts=%s alerts=%s duration=%.2fs",
                run.counts_by_check,
                alert_stats,
                run.duration_seconds,
            )
        return {
            "checked": len(run.results),
            "unhealthy": run.unhealthy,
            "counts_by_check": run.counts_by_check,
            "alerts": alert_stats,
            "duration_seconds": run.duration_seconds,
        }
    except Exception as e:
        status = "error"
        logger.exception("DOTMAC_ERP_IDENTITY_DRIFT_FAILED error=%s", str(e))
        session.rollback()
        raise
    finally:
        session.close()
        duration = time.monotonic() - start
        observe_job("dotmac_erp_identity_drift", status, duration)


@celery_app.task(
    name="app.tasks.integrations.sync_dotmac_erp_teams",
    time_limit=300,
    soft_time_limit=240,
)
def sync_dotmac_erp_teams():
    """Pull departments from DotMac ERP into ServiceTeam model.

    Syncs team structure and membership, auto-syncs CRM agents.

    Returns:
        Dict with sync result summary
    """
    from app.services.dotmac_erp import dotmac_erp_team_sync, record_team_sync_result

    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger = get_logger(__name__)
    logger.info("DOTMAC_ERP_TEAM_SYNC_START")

    try:
        sync_service = dotmac_erp_team_sync(session)
        result = sync_service.sync_departments()
        sync_service.close()

        logger.info(
            "DOTMAC_ERP_TEAM_SYNC_COMPLETE teams_created=%d teams_updated=%d "
            "teams_deactivated=%d members_added=%d members_updated=%d "
            "members_deactivated=%d persons_matched=%d persons_skipped=%d "
            "crm_agents_synced=%d errors=%d duration=%.2fs",
            result.teams_created,
            result.teams_updated,
            result.teams_deactivated,
            result.members_added,
            result.members_updated,
            result.members_deactivated,
            result.persons_matched,
            result.persons_skipped,
            result.crm_agents_synced,
            len(result.errors),
            result.duration_seconds,
        )

        if result.has_errors:
            status = "partial"
            for error in result.errors[:10]:
                logger.warning("DOTMAC_ERP_TEAM_SYNC_ERROR %s", error)

        record_team_sync_result(
            teams_created=result.teams_created,
            teams_updated=result.teams_updated,
            teams_deactivated=result.teams_deactivated,
            members_added=result.members_added,
            members_updated=result.members_updated,
            members_deactivated=result.members_deactivated,
            persons_matched=result.persons_matched,
            persons_skipped=result.persons_skipped,
            errors=result.errors,
            duration_seconds=result.duration_seconds,
        )

        return {
            "teams_created": result.teams_created,
            "teams_updated": result.teams_updated,
            "teams_deactivated": result.teams_deactivated,
            "members_added": result.members_added,
            "members_updated": result.members_updated,
            "members_deactivated": result.members_deactivated,
            "persons_matched": result.persons_matched,
            "persons_skipped": result.persons_skipped,
            "crm_agents_synced": result.crm_agents_synced,
            "total_synced": result.total_synced,
            "errors": result.errors,
            "duration_seconds": result.duration_seconds,
        }

    except Exception as e:
        status = "error"
        logger.exception("DOTMAC_ERP_TEAM_SYNC_FAILED error=%s", str(e))
        session.rollback()
        raise

    finally:
        session.close()
        duration = time.monotonic() - start
        observe_job("dotmac_erp_team_sync", status, duration)


@celery_app.task(
    name="app.tasks.integrations.sync_dotmac_erp_agents",
    time_limit=300,
    soft_time_limit=240,
)
def sync_dotmac_erp_agents():
    """Pull CRM agents from DotMac ERP employees in the configured department."""
    from app.services.dotmac_erp import dotmac_erp_agent_sync, record_agent_sync_result

    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger = get_logger(__name__)
    logger.info("DOTMAC_ERP_AGENT_SYNC_START")

    try:
        sync_service = dotmac_erp_agent_sync(session)
        result = sync_service.sync_all()
        sync_service.close()

        logger.info(
            "DOTMAC_ERP_AGENT_SYNC_COMPLETE persons_created=%d persons_updated=%d "
            "agents_created=%d agents_updated=%d agents_reactivated=%d "
            "agents_deactivated=%d employees_seen=%d employees_eligible=%d "
            "errors=%d duration=%.2fs",
            result.persons_created,
            result.persons_updated,
            result.agents_created,
            result.agents_updated,
            result.agents_reactivated,
            result.agents_deactivated,
            result.employees_seen,
            result.employees_eligible,
            len(result.errors),
            result.duration_seconds,
        )

        if result.has_errors:
            status = "partial"
            for error in result.errors[:10]:
                logger.warning("DOTMAC_ERP_AGENT_SYNC_ERROR %s", error)

        record_agent_sync_result(
            persons_created=result.persons_created,
            persons_updated=result.persons_updated,
            agents_created=result.agents_created,
            agents_updated=result.agents_updated,
            agents_reactivated=result.agents_reactivated,
            agents_deactivated=result.agents_deactivated,
            employees_seen=result.employees_seen,
            employees_eligible=result.employees_eligible,
            errors=result.errors,
            duration_seconds=result.duration_seconds,
        )

        return {
            "persons_created": result.persons_created,
            "persons_updated": result.persons_updated,
            "agents_created": result.agents_created,
            "agents_updated": result.agents_updated,
            "agents_reactivated": result.agents_reactivated,
            "agents_deactivated": result.agents_deactivated,
            "employees_seen": result.employees_seen,
            "employees_eligible": result.employees_eligible,
            "total_synced": result.total_synced,
            "errors": result.errors,
            "duration_seconds": result.duration_seconds,
        }

    except Exception as e:
        status = "error"
        logger.exception("DOTMAC_ERP_AGENT_SYNC_FAILED error=%s", str(e))
        session.rollback()
        raise

    finally:
        session.close()
        duration = time.monotonic() - start
        observe_job("dotmac_erp_agent_sync", status, duration)


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

        # Get Chatwoot configuration from settings
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
