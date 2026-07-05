"""Field-tech live location: ingest, current-snapshot store, live feed, retention.

Person-keyed (see docs/field-app-scope.md §9.1). Routes in app/api/field/ are thin
wrappers; this service owns validation, the presence snapshot, and the retention prune.
Geofence auto-status (task #46) and nearest-tech assignment (task #47) build on the
data this records.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.dispatch import TechnicianProfile
from app.models.domain_settings import DomainSetting, SettingDomain
from app.models.field_location import FieldPresenceStatus, FieldTechLocationPing, FieldTechPresence
from app.models.person import Person
from app.models.workforce import WorkOrder, WorkOrderStatus
from app.services.common import coerce_uuid, validate_enum

# Default retention for the immutable ping audit. Pings older than this are pruned;
# the current-snapshot row on FieldTechPresence is kept. Kept long enough (30 days)
# that admins can review movement history; overridable via the field DomainSetting
# ``location_ping_retention_hours`` (see resolved_retention_hours).
DEFAULT_PING_RETENTION_HOURS = 720
RETENTION_SETTING_KEY = "location_ping_retention_hours"
DEFAULT_STALE_AFTER_SECONDS = 120
MAX_BATCH_PINGS = 200

logger = logging.getLogger(__name__)


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

        # Geofence auto-status (task #46): a best-effort convenience over ingest —
        # never let it break the upload.
        transitions: list[dict] = []
        if presence.last_latitude is not None and presence.last_longitude is not None:
            try:
                from app.services.field import geofence

                transitions = geofence.evaluate(db, person_id, presence.last_latitude, presence.last_longitude)
                if transitions:
                    db.refresh(presence)
            except Exception:
                logger.exception("geofence_evaluate_failed person_id=%s", person_id)

        return {"accepted": accepted, "errors": errors, "presence": presence, "transitions": transitions}

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
    def list_tracking_states(
        db: Session,
        *,
        stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
        limit: int = 500,
    ) -> list[dict]:
        """All active technician tracking states for admin operations management."""
        safe_limit = max(1, min(int(limit or 500), 500))
        window = max(int(stale_after_seconds or DEFAULT_STALE_AFTER_SECONDS), 30)
        cutoff = _now() - timedelta(seconds=window)

        rows = (
            db.query(TechnicianProfile, Person, FieldTechPresence)
            .join(Person, Person.id == TechnicianProfile.person_id)
            .outerjoin(FieldTechPresence, FieldTechPresence.person_id == TechnicianProfile.person_id)
            .filter(TechnicianProfile.is_active.is_(True))
            .filter(Person.is_active.is_(True))
            .order_by(Person.last_name.asc(), Person.first_name.asc(), TechnicianProfile.created_at.desc())
            .limit(safe_limit)
            .all()
        )

        person_ids = [profile.person_id for profile, _person, _presence in rows]
        active_work_by_person: dict[str, WorkOrder] = {}
        if person_ids:
            active_orders = (
                db.query(WorkOrder)
                .filter(WorkOrder.is_active.is_(True))
                .filter(WorkOrder.assigned_to_person_id.in_(person_ids))
                .filter(
                    WorkOrder.status.in_(
                        [
                            WorkOrderStatus.scheduled,
                            WorkOrderStatus.dispatched,
                            WorkOrderStatus.in_progress,
                            WorkOrderStatus.paused,
                        ]
                    )
                )
                .order_by(WorkOrder.updated_at.desc())
                .all()
            )
            for order in active_orders:
                key = str(order.assigned_to_person_id)
                active_work_by_person.setdefault(key, order)

        items: list[dict] = []
        for profile, person, presence in rows:
            person_id = str(profile.person_id)
            last_location_at = presence.last_location_at if presence else None
            comparable_last_location_at = last_location_at
            if comparable_last_location_at is not None and comparable_last_location_at.tzinfo is None:
                comparable_last_location_at = comparable_last_location_at.replace(tzinfo=UTC)
            is_live = bool(
                presence
                and presence.location_sharing_enabled
                and comparable_last_location_at is not None
                and comparable_last_location_at >= cutoff
            )
            current_order: WorkOrder | None = active_work_by_person.get(person_id)
            items.append(
                {
                    "technician_id": str(profile.id),
                    "person_id": person_id,
                    "person_label": _person_label(person),
                    "title": profile.title,
                    "region": profile.region,
                    "status": presence.status.value if presence else FieldPresenceStatus.off_shift.value,
                    "location_sharing_enabled": bool(presence and presence.location_sharing_enabled),
                    "is_live": is_live,
                    "last_latitude": float(presence.last_latitude)
                    if presence and presence.last_latitude is not None
                    else None,
                    "last_longitude": (
                        float(presence.last_longitude) if presence and presence.last_longitude is not None else None
                    ),
                    "accuracy_m": (
                        float(presence.last_location_accuracy_m)
                        if presence and presence.last_location_accuracy_m is not None
                        else None
                    ),
                    "last_location_at": last_location_at,
                    "last_seen_at": presence.last_seen_at if presence else None,
                    "active_work_order": (
                        {
                            "id": str(current_order.id),
                            "title": current_order.title,
                            "status": current_order.status.value,
                            "work_type": current_order.work_type.value,
                        }
                        if current_order
                        else None
                    ),
                }
            )
        return items

    @staticmethod
    def recent_tracks(
        db: Session,
        *,
        window_minutes: int = 30,
        stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
        max_points_per_tech: int = 240,
        limit_techs: int = 200,
    ) -> list[dict]:
        """Recent breadcrumb trails for sharing-enabled, non-stale technicians.

        Powers the admin live map's movement view: each tech carries an ordered
        (oldest -> newest) list of their pings within the window, so the map can
        draw where they have been, not just where they are. Trimmed to the most
        recent ``max_points_per_tech`` points per tech.
        """
        window_minutes = max(1, min(int(window_minutes or 30), 24 * 60))
        safe_points = max(2, min(int(max_points_per_tech or 240), 1000))
        safe_limit = max(1, min(int(limit_techs or 200), 500))
        stale_window = max(int(stale_after_seconds or DEFAULT_STALE_AFTER_SECONDS), 30)
        now = _now()
        stale_cutoff = now - timedelta(seconds=stale_window)
        track_cutoff = now - timedelta(minutes=window_minutes)

        presence_rows = (
            db.query(FieldTechPresence, Person)
            .join(Person, Person.id == FieldTechPresence.person_id)
            .filter(FieldTechPresence.location_sharing_enabled.is_(True))
            .filter(FieldTechPresence.last_location_at.isnot(None))
            .filter(FieldTechPresence.last_location_at >= stale_cutoff)
            .order_by(FieldTechPresence.last_location_at.desc())
            .limit(safe_limit)
            .all()
        )
        if not presence_rows:
            return []

        person_ids = [presence.person_id for presence, _person in presence_rows]
        pings = (
            db.query(FieldTechLocationPing)
            .filter(FieldTechLocationPing.person_id.in_(person_ids))
            .filter(FieldTechLocationPing.captured_at >= track_cutoff)
            .order_by(FieldTechLocationPing.person_id, FieldTechLocationPing.captured_at.asc())
            .all()
        )
        points_by_person: dict[str, list[dict]] = {}
        for ping in pings:
            points_by_person.setdefault(str(ping.person_id), []).append(
                {
                    "latitude": float(ping.latitude),
                    "longitude": float(ping.longitude),
                    "accuracy_m": float(ping.accuracy_m) if ping.accuracy_m is not None else None,
                    "captured_at": ping.captured_at,
                }
            )

        items: list[dict] = []
        for presence, person in presence_rows:
            key = str(presence.person_id)
            points = points_by_person.get(key, [])
            if len(points) > safe_points:
                points = points[-safe_points:]
            items.append(
                {
                    "person_id": key,
                    "person_label": _person_label(person),
                    "status": presence.status.value,
                    "last_location_at": presence.last_location_at,
                    "points": points,
                }
            )
        return items

    @staticmethod
    def ping_history(
        db: Session,
        person_id: str,
        *,
        since: datetime,
        until: datetime | None = None,
        max_points: int = 5000,
    ) -> list[dict]:
        """Ordered (oldest -> newest) ping history for one technician over a time
        range — the data source for admin movement playback / audit."""
        person_uuid = coerce_uuid(person_id)
        until = until or _now()
        safe_points = max(1, min(int(max_points or 5000), 20000))
        rows = (
            db.query(FieldTechLocationPing)
            .filter(FieldTechLocationPing.person_id == person_uuid)
            .filter(FieldTechLocationPing.captured_at >= since)
            .filter(FieldTechLocationPing.captured_at <= until)
            .order_by(FieldTechLocationPing.captured_at.asc())
            .limit(safe_points)
            .all()
        )
        return [
            {
                "latitude": float(row.latitude),
                "longitude": float(row.longitude),
                "accuracy_m": float(row.accuracy_m) if row.accuracy_m is not None else None,
                "captured_at": row.captured_at,
                "work_order_id": str(row.work_order_id) if row.work_order_id else None,
            }
            for row in rows
        ]

    @staticmethod
    def resolved_retention_hours(db: Session) -> int:
        """Retention window for the ping audit, overridable via the field
        DomainSetting ``location_ping_retention_hours`` so movement history can be
        kept longer (or shorter) for review without a code change."""
        row = (
            db.query(DomainSetting)
            .filter(DomainSetting.domain == SettingDomain.field)
            .filter(DomainSetting.key == RETENTION_SETTING_KEY)
            .filter(DomainSetting.is_active.is_(True))
            .first()
        )
        if row is not None:
            raw = row.value_json if row.value_json is not None else row.value_text
            try:
                hours = int(str(raw).strip())
            except (TypeError, ValueError):
                hours = 0
            if hours >= 1:
                return hours
        return DEFAULT_PING_RETENTION_HOURS

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
