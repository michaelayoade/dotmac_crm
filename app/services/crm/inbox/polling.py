"""
Email polling job management for CRM inbox integration.

This module handles the creation and management of email polling jobs,
as well as the execution of polling operations against email connectors.
"""

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.models.connector import ConnectorConfig, ConnectorType
from app.models.integration import (
    IntegrationJob,
    IntegrationJobType,
    IntegrationScheduleType,
    IntegrationTarget,
    IntegrationTargetType,
)
from app.services.common import coerce_uuid
from app.services.crm import email_polling

logger = get_logger(__name__)


def ensure_email_polling_job(
    db: Session,
    target_id: str,
    interval_seconds: int | None = None,
    interval_minutes: int | None = None,
    name: str | None = None,
) -> IntegrationJob:
    """
    Ensure an email polling job exists for the given integration target.

    Creates a new polling job if one doesn't exist, or updates the existing
    job's interval settings if it does. The job is configured to poll the
    email connector associated with the target at the specified interval.

    Args:
        db: Database session.
        target_id: UUID of the integration target to create/update polling for.
        interval_seconds: Polling interval in seconds (mutually exclusive with interval_minutes).
        interval_minutes: Polling interval in minutes (mutually exclusive with interval_seconds).
        name: Optional name for the job. Defaults to "{target.name} Email Polling".

    Returns:
        The created or updated IntegrationJob instance.

    Raises:
        HTTPException: 400 if interval is invalid, target is wrong type, or missing connector.
        HTTPException: 404 if integration target not found.
    """
    if interval_minutes is not None:
        if interval_minutes < 1:
            raise HTTPException(status_code=400, detail="interval_minutes must be >= 1")
    elif interval_seconds is not None:
        if interval_seconds < 1:
            raise HTTPException(status_code=400, detail="interval_seconds must be >= 1")
    else:
        raise HTTPException(status_code=400, detail="interval_seconds must be >= 1")

    target = db.get(IntegrationTarget, coerce_uuid(target_id))
    if not target:
        raise HTTPException(status_code=404, detail="Integration target not found")
    if target.target_type != IntegrationTargetType.crm:
        raise HTTPException(status_code=400, detail="Target must be crm type")
    if not target.connector_config_id:
        raise HTTPException(status_code=400, detail="Target missing connector config")

    config = db.get(ConnectorConfig, target.connector_config_id)
    if not config or config.connector_type != ConnectorType.email:
        raise HTTPException(status_code=400, detail="Target is not email connector")

    interval_seconds_value = interval_seconds if interval_minutes is None else None
    interval_minutes_value = interval_minutes

    job = (
        db.query(IntegrationJob)
        .filter(IntegrationJob.target_id == target.id)
        .filter(IntegrationJob.job_type == IntegrationJobType.import_)
        .order_by(IntegrationJob.created_at.desc())
        .first()
    )

    if job:
        changed = (
            job.interval_minutes != interval_minutes_value
            or job.interval_seconds != interval_seconds_value
            or job.schedule_type != IntegrationScheduleType.interval
            or job.is_active is not True
        )
        if changed:
            logger.info("EMAIL_POLL_JOB_UPDATED job_id=%s target_id=%s", job.id, target.id)
        else:
            logger.info("EMAIL_POLL_JOB_SKIPPED job_id=%s target_id=%s", job.id, target.id)

        if interval_minutes_value is not None:
            job.interval_minutes = interval_minutes_value
            job.interval_seconds = None
        else:
            job.interval_seconds = interval_seconds_value
            job.interval_minutes = None

        job.schedule_type = IntegrationScheduleType.interval
        job.is_active = True
        db.commit()
        db.refresh(job)

        logger.info(
            "EMAIL_POLL_JOB_CALLED connector_id=%s interval_seconds=%s interval_minutes=%s job_id=%s",
            config.id,
            interval_seconds_value,
            interval_minutes_value,
            job.id,
        )
        return job

    job = IntegrationJob(
        target_id=target.id,
        name=name or f"{target.name} Email Polling",
        job_type=IntegrationJobType.import_,
        schedule_type=IntegrationScheduleType.interval,
        interval_seconds=interval_seconds_value,
        interval_minutes=interval_minutes_value,
        is_active=True,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    logger.info("EMAIL_POLL_JOB_CREATED job_id=%s target_id=%s", job.id, target.id)
    logger.info(
        "EMAIL_POLL_JOB_CALLED connector_id=%s interval_seconds=%s interval_minutes=%s job_id=%s",
        config.id,
        interval_seconds_value,
        interval_minutes_value,
        job.id,
    )
    return job


def poll_email_targets(db: Session, target_id: str | None = None) -> dict:
    """
    Poll email targets for new messages.

    Queries all active CRM integration targets with email connectors and
    polls each one for new messages. Optionally filters to a specific target.

    Args:
        db: Database session.
        target_id: Optional UUID of a specific target to poll. If None, polls all
            active CRM targets with email connectors.

    Returns:
        A dictionary with the total number of messages processed:
        {"processed": int}
    """
    query = db.query(IntegrationTarget).filter(
        IntegrationTarget.target_type == IntegrationTargetType.crm,
        IntegrationTarget.is_active.is_(True),
    )
    if target_id:
        query = query.filter(IntegrationTarget.id == coerce_uuid(target_id))

    targets = query.all()
    if not targets:
        logger.info("EMAIL_POLL_EXIT reason=no_targets")
        return {"processed": 0}

    email_connectors: list[ConnectorConfig] = []
    for target in targets:
        if not target.connector_config_id:
            continue
        config = db.get(ConnectorConfig, target.connector_config_id)
        if not config or config.connector_type != ConnectorType.email:
            continue
        email_connectors.append(config)

    logger.info(
        "EMAIL_POLL_START ts=%s targets=%s connectors=%s",
        datetime.now(timezone.utc).isoformat(),
        len(targets),
        len(email_connectors),
    )

    if not email_connectors:
        logger.info("EMAIL_POLL_EXIT reason=no_connectors")
        return {"processed": 0}

    processed_total = 0
    errors: list[dict[str, str]] = []
    for config in email_connectors:
        try:
            result = email_polling.poll_email_connector(db, config)
            processed_total += int(result.get("processed") or 0)
        except Exception as exc:
            logger.info(
                "EMAIL_POLL_EXIT reason=connector_failure connector_id=%s error=%s",
                config.id,
                exc,
            )
            errors.append({"connector_id": str(config.id), "error": str(exc)})
            continue

    return {"processed": processed_total, "errors": errors}
