"""Merged schedule timeline for the field app: shifts, availability, jobs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.dispatch import AvailabilityBlock, Shift, TechnicianProfile
from app.models.workforce import WorkOrder
from app.services.common import coerce_uuid
from app.services.field.jobs import _scoped_query

_DEFAULT_WINDOW_DAYS = 7
_MAX_WINDOW_DAYS = 31


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class FieldSchedule:
    @staticmethod
    def timeline(
        db: Session,
        person_id: str,
        *,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[dict]:
        now = datetime.now(UTC)
        start = _as_utc(date_from) if date_from else now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = _as_utc(date_to) if date_to else start + timedelta(days=_DEFAULT_WINDOW_DAYS)
        if end <= start:
            raise HTTPException(status_code=422, detail="'to' must be after 'from'")
        if (end - start) > timedelta(days=_MAX_WINDOW_DAYS):
            end = start + timedelta(days=_MAX_WINDOW_DAYS)

        person_uuid = coerce_uuid(person_id)
        entries: list[dict] = []

        # Vendor users and techs without a profile still get their job timeline.
        profile = (
            db.query(TechnicianProfile)
            .filter(TechnicianProfile.person_id == person_uuid)
            .filter(TechnicianProfile.is_active.is_(True))
            .first()
        )
        if profile:
            shifts = (
                db.query(Shift)
                .filter(Shift.technician_id == profile.id)
                .filter(Shift.is_active.is_(True))
                .filter(Shift.end_at >= start)
                .filter(Shift.start_at <= end)
                .all()
            )
            entries.extend(
                {
                    "type": "shift",
                    "start_at": _as_utc(s.start_at),
                    "end_at": _as_utc(s.end_at),
                    "title": s.shift_type or "Shift",
                    "reference_id": s.id,
                }
                for s in shifts
            )
            blocks = (
                db.query(AvailabilityBlock)
                .filter(AvailabilityBlock.technician_id == profile.id)
                .filter(AvailabilityBlock.is_active.is_(True))
                .filter(AvailabilityBlock.end_at >= start)
                .filter(AvailabilityBlock.start_at <= end)
                .all()
            )
            entries.extend(
                {
                    "type": "availability",
                    "start_at": _as_utc(b.start_at),
                    "end_at": _as_utc(b.end_at),
                    "title": b.reason or b.block_type or "Unavailable",
                    "reference_id": b.id,
                }
                for b in blocks
            )

        jobs = (
            _scoped_query(db, person_uuid)
            .filter(WorkOrder.scheduled_start.isnot(None))
            .filter(WorkOrder.scheduled_start >= start)
            .filter(WorkOrder.scheduled_start <= end)
            .all()
        )
        entries.extend(
            {
                "type": "job",
                "start_at": _as_utc(wo.scheduled_start),
                "end_at": _as_utc(wo.scheduled_end) if wo.scheduled_end else None,
                "title": wo.title,
                "reference_id": wo.id,
            }
            for wo in jobs
        )

        entries.sort(key=lambda e: e["start_at"])
        return entries


field_schedule = FieldSchedule()
