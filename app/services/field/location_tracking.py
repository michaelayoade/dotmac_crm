"""Field-tech live location: ingest, current-snapshot store, live feed, retention.

Person-keyed (see docs/field-app-scope.md §9.1). Routes in app/api/field/ are thin
wrappers; this service owns validation, the presence snapshot, and the retention prune.
Geofence auto-status (task #46) and nearest-tech assignment (task #47) build on the
data this records.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.field_location import FieldPresenceStatus, FieldTechLocationPing, FieldTechPresence
from app.models.person import Person
from app.services.common import coerce_uuid, validate_enum

# Default retention for the immutable ping audit. Pings older than this are pruned;
# the current-snapshot row on FieldTechPresence is kept.
DEFAULT_PING_RETENTION_HOURS = 72
DEFAULT_STALE_AFTER_SECONDS = 120
MAX_BATCH_PINGS = 200


def _now() -> datetime:
    return datetime.now(UTC)


def _coerce_captured_at(value: str | datetime | None) -> datetime:
    if value is None:
        return _now()
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid captured_at timestamp") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _validate_coords(latitude: float, longitude: float) -> None:
    if not (-90.0 <= latitude <= 90.0):
        raise HTTPException(status_code=422, detail="latitude out of range")
    if not (-180.0 <= longitude <= 180.0):
        raise HTTPException(status_code=422, detail="longitude out of range")


def _person_label(person: Person | None) -> str:
    if person is None:
        return "Technician"
    if person.display_name:
        return person.display_name
    full = f"{person.first_name or ''} {person.last_name or ''}".strip()
    return full or "Technician"


class FieldLocationTracking:
    @staticmethod
    def get_or_create_presence(db: Session, person_id: str) -> FieldTechPresence:
        person_uuid = coerce_uuid(person_id)
        presence = db.query(FieldTechPresence).filter(FieldTechPresence.person_id == person_uuid).first()
        if presence is None:
            presence = FieldTechPresence(person_id=person_uuid)
            db.add(presence)
            db.flush()
        return presence

    @staticmethod
    def set_sharing(
        db: Session,
        person_id: str,
        *,
        enabled: bool,
        status: str | None = None,
    ) -> FieldTechPresence:
        presence = FieldLocationTracking.get_or_create_presence(db, person_id)
        presence.location_sharing_enabled = bool(enabled)
        if status is not None:
            presence.status = validate_enum(status, FieldPresenceStatus, "status")
        elif not enabled:
            presence.status = FieldPresenceStatus.off_shift
        presence.last_seen_at = _now()
        db.commit()
        db.refresh(presence)
        return presence

    @staticmethod
    def record_ping(
        db: Session,
        person_id: str,
        *,
        latitude: float,
        longitude: float,
        accuracy_m: float | None = None,
        captured_at: str | datetime | None = None,
        work_order_id: str | None = None,
        source: str = "mobile",
        status: str | None = None,
        commit: bool = True,
    ) -> dict:
        """Record one ping and roll it into the tech's current snapshot.

        Out-of-order pings (a later-arriving older fix) never roll the snapshot
        backwards: the snapshot only advances when the ping is newer than what we
        already hold.
        """
        _validate_coords(latitude, longitude)
        person_uuid = coerce_uuid(person_id)
        captured = _coerce_captured_at(captured_at)
        now = _now()

        ping = FieldTechLocationPing(
            person_id=person_uuid,
            latitude=float(latitude),
            longitude=float(longitude),
            accuracy_m=float(accuracy_m) if accuracy_m is not None else None,
            work_order_id=coerce_uuid(work_order_id) if work_order_id else None,
            captured_at=captured,
            received_at=now,
            source=source or "mobile",
        )
        db.add(ping)

        presence = FieldLocationTracking.get_or_create_presence(db, person_id)
        presence.last_seen_at = now
        if status is not None:
            presence.status = validate_enum(status, FieldPresenceStatus, "status")
        # Only advance the snapshot for a fresher fix. SQLite reads timestamps
        # back as naive, so normalise before comparing.
        prior = presence.last_location_at
        if prior is not None and prior.tzinfo is None:
            prior = prior.replace(tzinfo=UTC)
        if prior is None or captured >= prior:
            presence.last_latitude = float(latitude)
            presence.last_longitude = float(longitude)
            presence.last_location_accuracy_m = float(accuracy_m) if accuracy_m is not None else None
            presence.last_location_at = captured

        if commit:
            db.commit()
            db.refresh(ping)
            db.refresh(presence)
        else:
            db.flush()
        return {"ping": ping, "presence": presence}

    @staticmethod
    def record_batch(db: Session, person_id: str, pings: list[dict]) -> dict:
        """Ingest a batch of offline-queued pings. Per-ping validation errors are
        collected, not fatal — one bad fix never drops the whole upload."""
        if len(pings) > MAX_BATCH_PINGS:
            raise HTTPException(status_code=422, detail=f"Batch exceeds {MAX_BATCH_PINGS} pings")
        accepted = 0
        errors: list[dict] = []
        last: dict | None = None
        for index, raw in enumerate(pings):
            try:
                last = FieldLocationTracking.record_ping(
                    db,
                    person_id,
                    latitude=raw["latitude"],
                    longitude=raw["longitude"],
                    accuracy_m=raw.get("accuracy_m"),
                    captured_at=raw.get("captured_at"),
                    work_order_id=raw.get("work_order_id"),
                    source=raw.get("source", "mobile"),
                    status=raw.get("status"),
                    commit=False,
                )
                accepted += 1
            except HTTPException as exc:
                errors.append({"index": index, "detail": exc.detail})
            except (KeyError, TypeError, ValueError) as exc:
                errors.append({"index": index, "detail": str(exc)})
        db.commit()
        presence = last["presence"] if last else FieldLocationTracking.get_or_create_presence(db, person_id)
        db.refresh(presence)
        return {"accepted": accepted, "errors": errors, "presence": presence}

    @staticmethod
    def list_live_locations(
        db: Session,
        *,
        stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
        limit: int = 200,
    ) -> list[dict]:
        """Sharing-enabled, non-stale techs for the admin live-map feed."""
        safe_limit = max(1, min(int(limit or 200), 500))
        window = max(int(stale_after_seconds or DEFAULT_STALE_AFTER_SECONDS), 30)
        cutoff = _now() - timedelta(seconds=window)
        rows = (
            db.query(FieldTechPresence, Person)
            .join(Person, Person.id == FieldTechPresence.person_id)
            .filter(FieldTechPresence.location_sharing_enabled.is_(True))
            .filter(FieldTechPresence.last_location_at.isnot(None))
            .filter(FieldTechPresence.last_location_at >= cutoff)
            .order_by(FieldTechPresence.last_location_at.desc())
            .limit(safe_limit)
            .all()
        )
        items: list[dict] = []
        for presence, person in rows:
            items.append(
                {
                    "person_id": str(presence.person_id),
                    "person_label": _person_label(person),
                    "status": presence.status.value,
                    "latitude": float(presence.last_latitude) if presence.last_latitude is not None else None,
                    "longitude": float(presence.last_longitude) if presence.last_longitude is not None else None,
                    "accuracy_m": (
                        float(presence.last_location_accuracy_m)
                        if presence.last_location_accuracy_m is not None
                        else None
                    ),
                    "last_location_at": presence.last_location_at,
                    "last_seen_at": presence.last_seen_at,
                }
            )
        return items

    @staticmethod
    def prune_pings(db: Session, *, older_than_hours: int = DEFAULT_PING_RETENTION_HOURS) -> int:
        cutoff = _now() - timedelta(hours=max(int(older_than_hours or DEFAULT_PING_RETENTION_HOURS), 1))
        deleted = (
            db.query(FieldTechLocationPing)
            .filter(FieldTechLocationPing.received_at < cutoff)
            .delete(synchronize_session=False)
        )
        db.commit()
        return int(deleted or 0)


field_location_tracking = FieldLocationTracking()
