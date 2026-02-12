from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.models.connector import ConnectorConfig, ConnectorType
from app.models.integration import (
    IntegrationJob,
    IntegrationJobType,
    IntegrationRun,
    IntegrationRunStatus,
    IntegrationScheduleType,
    IntegrationTarget,
    IntegrationTargetType,
)
from app.schemas.integration import (
    IntegrationJobCreate,
    IntegrationJobUpdate,
    IntegrationTargetCreate,
    IntegrationTargetUpdate,
)
from app.services.common import apply_ordering, apply_pagination, coerce_uuid, validate_enum
from app.services.response import ListResponseMixin

logger = get_logger(__name__)


class IntegrationTargets(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: IntegrationTargetCreate):
        if payload.connector_config_id:
            config = db.get(ConnectorConfig, payload.connector_config_id)
            if not config:
                raise HTTPException(status_code=404, detail="Connector config not found")
        target = IntegrationTarget(**payload.model_dump())
        db.add(target)
        db.commit()
        db.refresh(target)
        return target

    @staticmethod
    def get(db: Session, target_id: str):
        target = db.get(IntegrationTarget, coerce_uuid(target_id))
        if not target:
            raise HTTPException(status_code=404, detail="Integration target not found")
        return target

    @staticmethod
    def list(
        db: Session,
        target_type: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(IntegrationTarget)
        if target_type:
            query = query.filter(
                IntegrationTarget.target_type == validate_enum(target_type, IntegrationTargetType, "target_type")
            )
        if is_active is None:
            query = query.filter(IntegrationTarget.is_active.is_(True))
        else:
            query = query.filter(IntegrationTarget.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": IntegrationTarget.created_at, "name": IntegrationTarget.name},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def list_all(
        db: Session,
        target_type: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(IntegrationTarget)
        if target_type:
            query = query.filter(
                IntegrationTarget.target_type == validate_enum(target_type, IntegrationTargetType, "target_type")
            )
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": IntegrationTarget.created_at, "name": IntegrationTarget.name},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, target_id: str, payload: IntegrationTargetUpdate):
        target = db.get(IntegrationTarget, coerce_uuid(target_id))
        if not target:
            raise HTTPException(status_code=404, detail="Integration target not found")
        data = payload.model_dump(exclude_unset=True)
        if data.get("connector_config_id"):
            config = db.get(ConnectorConfig, data["connector_config_id"])
            if not config:
                raise HTTPException(status_code=404, detail="Connector config not found")
        for key, value in data.items():
            setattr(target, key, value)
        db.commit()
        db.refresh(target)
        return target

    @staticmethod
    def delete(db: Session, target_id: str):
        target = db.get(IntegrationTarget, coerce_uuid(target_id))
        if not target:
            raise HTTPException(status_code=404, detail="Integration target not found")
        target.is_active = False
        db.commit()

    @staticmethod
    def get_channel_state(
        db: Session,
        target_type: IntegrationTargetType,
        connector_type: ConnectorType,
    ) -> dict | None:
        """Get channel state for CRM integration (email or whatsapp)."""
        target = (
            db.query(IntegrationTarget)
            .join(ConnectorConfig, ConnectorConfig.id == IntegrationTarget.connector_config_id)
            .filter(IntegrationTarget.target_type == target_type)
            .filter(IntegrationTarget.is_active.is_(True))
            .filter(ConnectorConfig.connector_type == connector_type)
            .order_by(IntegrationTarget.created_at.desc())
            .first()
        )
        if not target or not target.connector_config:
            return None

        config = target.connector_config
        metadata = config.metadata_ if isinstance(config.metadata_, dict) else {}
        auth_config = config.auth_config if isinstance(config.auth_config, dict) else {}

        result = {
            "target_id": str(target.id),
            "connector_id": str(config.id),
            "name": target.name or config.name,
            "auth_config": auth_config,
        }

        if connector_type == ConnectorType.email:
            job = (
                db.query(IntegrationJob)
                .filter(IntegrationJob.target_id == target.id)
                .filter(IntegrationJob.job_type == IntegrationJobType.import_)
                .order_by(IntegrationJob.created_at.desc())
                .first()
            )
            smtp = metadata.get("smtp")
            imap = metadata.get("imap")
            pop3 = metadata.get("pop3")
            poll_interval = None
            if job:
                if job.interval_seconds is not None:
                    poll_interval = job.interval_seconds
                elif job.interval_minutes:
                    poll_interval = job.interval_minutes * 60
            result.update(
                {
                    "smtp": smtp,
                    "imap": imap,
                    "pop3": pop3,
                    "poll_interval_seconds": poll_interval,
                    "polling_active": bool(job and job.is_active),
                    "receiving_enabled": bool((imap or pop3) and job and job.is_active),
                }
            )
        elif connector_type == ConnectorType.whatsapp:
            result.update(
                {
                    "base_url": config.base_url,
                    "phone_number_id": metadata.get("phone_number_id"),
                }
            )

        return result


class IntegrationJobs(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: IntegrationJobCreate):
        target = db.get(IntegrationTarget, payload.target_id)
        if not target:
            raise HTTPException(status_code=404, detail="Integration target not found")
        job = IntegrationJob(**payload.model_dump())
        db.add(job)
        db.commit()
        db.refresh(job)
        return job

    @staticmethod
    def get(db: Session, job_id: str):
        job = db.get(IntegrationJob, coerce_uuid(job_id))
        if not job:
            raise HTTPException(status_code=404, detail="Integration job not found")
        return job

    @staticmethod
    def list(
        db: Session,
        target_id: str | None,
        job_type: str | None,
        schedule_type: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(IntegrationJob)
        if target_id:
            query = query.filter(IntegrationJob.target_id == target_id)
        if job_type:
            query = query.filter(IntegrationJob.job_type == validate_enum(job_type, IntegrationJobType, "job_type"))
        if schedule_type:
            query = query.filter(
                IntegrationJob.schedule_type == validate_enum(schedule_type, IntegrationScheduleType, "schedule_type")
            )
        if is_active is None:
            query = query.filter(IntegrationJob.is_active.is_(True))
        else:
            query = query.filter(IntegrationJob.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": IntegrationJob.created_at, "name": IntegrationJob.name},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def list_all(
        db: Session,
        target_id: str | None,
        job_type: str | None,
        schedule_type: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(IntegrationJob)
        if target_id:
            query = query.filter(IntegrationJob.target_id == target_id)
        if job_type:
            query = query.filter(IntegrationJob.job_type == validate_enum(job_type, IntegrationJobType, "job_type"))
        if schedule_type:
            query = query.filter(
                IntegrationJob.schedule_type == validate_enum(schedule_type, IntegrationScheduleType, "schedule_type")
            )
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": IntegrationJob.created_at, "name": IntegrationJob.name},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, job_id: str, payload: IntegrationJobUpdate):
        job = db.get(IntegrationJob, coerce_uuid(job_id))
        if not job:
            raise HTTPException(status_code=404, detail="Integration job not found")
        data = payload.model_dump(exclude_unset=True)
        if "target_id" in data:
            target = db.get(IntegrationTarget, data["target_id"])
            if not target:
                raise HTTPException(status_code=404, detail="Integration target not found")
        for key, value in data.items():
            setattr(job, key, value)
        db.commit()
        db.refresh(job)
        return job

    @staticmethod
    def delete(db: Session, job_id: str):
        job = db.get(IntegrationJob, coerce_uuid(job_id))
        if not job:
            raise HTTPException(status_code=404, detail="Integration job not found")
        job.is_active = False
        db.commit()

    @staticmethod
    def disable_import_jobs_for_target(db: Session, target_id: str) -> int:
        """Disable all active import jobs for a target."""
        count = (
            db.query(IntegrationJob)
            .filter(IntegrationJob.target_id == target_id)
            .filter(IntegrationJob.job_type == IntegrationJobType.import_)
            .filter(IntegrationJob.is_active.is_(True))
            .update({"is_active": False})
        )
        db.commit()
        return count

    @staticmethod
    def run(db: Session, job_id: str):
        job = db.get(IntegrationJob, coerce_uuid(job_id))
        if not job:
            raise HTTPException(status_code=404, detail="Integration job not found")
        if not job.is_active:
            logger.info("EMAIL_POLL_EXIT reason=job_disabled job_id=%s", job_id)
        run = IntegrationRun(job_id=job.id, status=IntegrationRunStatus.running)
        db.add(run)
        db.commit()
        db.refresh(run)
        try:
            metrics = None
            if job.target and job.target.connector_config_id:
                config = db.get(ConnectorConfig, job.target.connector_config_id)
                if config and config.connector_type == ConnectorType.email:
                    from app.services.crm import inbox as crm_inbox_service

                    logger.info(
                        "EMAIL_POLL_SCHEDULER_ENTRY job_id=%s target_id=%s",
                        job_id,
                        job.target_id,
                    )
                    metrics = crm_inbox_service.poll_email_targets(db, target_id=str(job.target_id))
            run.status = IntegrationRunStatus.success
            run.metrics = metrics
        except Exception as exc:
            run.status = IntegrationRunStatus.failed
            run.error = str(exc)
            raise
        finally:
            run.finished_at = datetime.now(UTC)
            job.last_run_at = run.finished_at
            db.commit()
            db.refresh(run)
        return run


class IntegrationRuns(ListResponseMixin):
    @staticmethod
    def list(
        db: Session,
        job_id: str | None,
        status: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(IntegrationRun)
        if job_id:
            query = query.filter(IntegrationRun.job_id == job_id)
        if status:
            query = query.filter(IntegrationRun.status == validate_enum(status, IntegrationRunStatus, "status"))
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {
                "created_at": IntegrationRun.created_at,
                "status": IntegrationRun.status,
                "started_at": IntegrationRun.started_at,
            },
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def get(db: Session, run_id: str):
        run = db.get(IntegrationRun, coerce_uuid(run_id))
        if not run:
            raise HTTPException(status_code=404, detail="Integration run not found")
        return run


integration_targets = IntegrationTargets()
integration_jobs = IntegrationJobs()
integration_runs = IntegrationRuns()


def list_interval_jobs(db: Session) -> list[IntegrationJob]:
    return (
        db.query(IntegrationJob)
        .filter(IntegrationJob.is_active.is_(True))
        .filter(IntegrationJob.schedule_type == IntegrationScheduleType.interval)
        .filter((IntegrationJob.interval_seconds.isnot(None)) | (IntegrationJob.interval_minutes.isnot(None)))
        .all()
    )


def refresh_schedule(db: Session) -> dict[str, object]:
    count = len(list_interval_jobs(db))
    return {
        "scheduled_jobs": count,
        "detail": "Celery beat loads schedules at startup. Restart beat to apply changes.",
    }


def reset_stuck_runs(db: Session, target_id: str) -> int:
    """Reset stuck running runs for a target's import job.

    Returns the number of runs reset.
    """
    job = (
        db.query(IntegrationJob)
        .filter(IntegrationJob.target_id == coerce_uuid(target_id))
        .filter(IntegrationJob.job_type == IntegrationJobType.import_)
        .order_by(IntegrationJob.created_at.desc())
        .first()
    )
    if not job:
        return 0

    count = (
        db.query(IntegrationRun)
        .filter(
            IntegrationRun.job_id == job.id,
            IntegrationRun.status == IntegrationRunStatus.running,
        )
        .update(
            {
                "status": IntegrationRunStatus.failed,
                "finished_at": datetime.now(UTC),
                "error": "reset via inbox settings",
            },
            synchronize_session=False,
        )
    )
    db.commit()
    return count
