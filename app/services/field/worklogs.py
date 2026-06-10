"""Field worklog service. Wraps app/services/timecost — never duplicates it.

Adds what the field app needs on top of the raw CRUD: caller scoping,
offline-batch submission with overlap/duration validation, backdated-entry
flagging, and timer auto-stop on hold/complete.

Offline idempotency note: WorkLog has no client_ref column; an exact
(person, work_order, start_at) match is treated as a duplicate instead. Two
distinct logs can never legitimately share all three, so this dedupes retried
uploads without a migration.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.timecost import WorkLog
from app.schemas.timecost import WorkLogCreate
from app.services import timecost as timecost_service
from app.services.common import coerce_uuid
from app.services.field.jobs import get_scoped_work_order

_MAX_DURATION_HOURS = 16
_BACKDATED_FLAG_DAYS = 7


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def stop_open_worklog(
    db: Session, work_order_id: UUID, person_id: UUID, *, stopped_at: datetime | None = None
) -> WorkLog | None:
    """Close the caller's running timer on a job, if any.

    Called by the transition service on hold/complete so timers never run
    overnight or past completion.
    """
    stopped_at = stopped_at or datetime.now(UTC)
    log = (
        db.query(WorkLog)
        .filter(WorkLog.work_order_id == work_order_id)
        .filter(WorkLog.person_id == person_id)
        .filter(WorkLog.end_at.is_(None))
        .filter(WorkLog.is_active.is_(True))
        .order_by(WorkLog.start_at.desc())
        .first()
    )
    if not log:
        return None
    log.end_at = stopped_at
    log.minutes = max(0, int((stopped_at - _as_utc(log.start_at)).total_seconds() // 60))
    db.commit()
    db.refresh(log)
    return log


def _find_duplicate(db: Session, person_uuid: UUID, work_order_id: UUID, start_at: datetime) -> WorkLog | None:
    candidates = (
        db.query(WorkLog)
        .filter(WorkLog.person_id == person_uuid)
        .filter(WorkLog.work_order_id == work_order_id)
        .filter(WorkLog.is_active.is_(True))
        .all()
    )
    for log in candidates:
        if _as_utc(log.start_at) == start_at:
            return log
    return None


def _check_overlap(db: Session, person_uuid: UUID, start_at: datetime, end_at: datetime | None) -> None:
    query = (
        db.query(WorkLog)
        .filter(WorkLog.person_id == person_uuid)
        .filter(WorkLog.is_active.is_(True))
    )
    if end_at is not None:
        query = query.filter(or_(WorkLog.end_at.is_(None), WorkLog.end_at > start_at))
        candidates = query.all()
        for log in candidates:
            log_start = _as_utc(log.start_at)
            log_end = _as_utc(log.end_at) if log.end_at else None
            if log_start < end_at and (log_end is None or log_end > start_at):
                raise HTTPException(status_code=409, detail="Worklog overlaps an existing entry")
    else:
        # Opening a timer: no other open timer, and no closed log covering now.
        open_log = query.filter(WorkLog.end_at.is_(None)).first()
        if open_log:
            raise HTTPException(status_code=409, detail="A timer is already running")


class FieldWorkLogs:
    @staticmethod
    def submit(
        db: Session,
        person_id: str,
        work_order_id: str,
        entries: list[dict],
    ) -> list[dict]:
        """Record one or more worklog entries for the caller on a job.

        Each entry: {start_at, end_at?, notes?}. end_at=None opens a timer
        (at most one). Backdated entries are accepted and flagged.
        """
        work_order = get_scoped_work_order(db, person_id, work_order_id)
        person_uuid = coerce_uuid(person_id)
        now = datetime.now(UTC)
        results: list[dict] = []

        for entry in entries:
            start_at = entry.get("start_at")
            if not isinstance(start_at, datetime):
                raise HTTPException(status_code=422, detail="start_at is required")
            start_at = _as_utc(start_at)
            end_at = entry.get("end_at")
            end_at = _as_utc(end_at) if isinstance(end_at, datetime) else None

            if end_at is not None:
                if end_at <= start_at:
                    raise HTTPException(status_code=422, detail="end_at must be after start_at")
                if (end_at - start_at) > timedelta(hours=_MAX_DURATION_HOURS):
                    raise HTTPException(
                        status_code=422,
                        detail=f"Worklog exceeds maximum duration of {_MAX_DURATION_HOURS} hours",
                    )

            duplicate = _find_duplicate(db, person_uuid, work_order.id, start_at)
            if duplicate:
                results.append({"worklog": duplicate, "duplicate": True, "backdated": False})
                continue

            _check_overlap(db, person_uuid, start_at, end_at)

            backdated = (now - start_at) > timedelta(days=_BACKDATED_FLAG_DAYS)
            log = timecost_service.work_logs.create(
                db,
                WorkLogCreate(
                    work_order_id=work_order.id,
                    person_id=person_uuid,
                    start_at=start_at,
                    end_at=end_at,
                    notes=entry.get("notes"),
                ),
            )
            results.append({"worklog": log, "duplicate": False, "backdated": backdated})

        return results


field_worklogs = FieldWorkLogs()
