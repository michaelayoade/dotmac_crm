"""Agent presence tracking for CRM."""

from __future__ import annotations

import builtins
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.crm.enums import AgentPresenceStatus
from app.models.crm.presence import AgentLocationPing, AgentPresence, AgentPresenceEvent
from app.models.crm.team import CrmAgent
from app.services.common import coerce_uuid, validate_enum
from app.services.response import ListResponseMixin

DEFAULT_STALE_MINUTES = 5
DEFAULT_LOCATION_STALE_SECONDS = 120
DEFAULT_LOCATION_RETENTION_HOURS = 48
_PRUNE_INTERVAL_SECONDS = 300  # Only prune old pings every 5 minutes
_last_prune_at: datetime | None = None


class AgentPresenceManager(ListResponseMixin):
    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _effective_status_for_row(
        presence: AgentPresence,
        *,
        stale_after_minutes: int = DEFAULT_STALE_MINUTES,
    ) -> AgentPresenceStatus:
        # Preserve existing behavior: stale or missing heartbeat => offline.
        if not presence.last_seen_at:
            return AgentPresenceStatus.offline
        last_seen_at = presence.last_seen_at
        if last_seen_at.tzinfo is None:
            last_seen_at = last_seen_at.replace(tzinfo=UTC)
        cutoff = AgentPresenceManager._now() - timedelta(minutes=stale_after_minutes)
        if last_seen_at < cutoff:
            return AgentPresenceStatus.offline

        # Manual override wins when heartbeat is fresh.
        if presence.manual_override_status is not None:
            return presence.manual_override_status

        return presence.status

    @staticmethod
    def _set_event_status(
        db: Session,
        *,
        agent_uuid,
        new_status: AgentPresenceStatus,
        source: str,
        now: datetime,
    ) -> None:
        # Only write when a status transition occurs (heartbeats shouldn't spam this table).
        current = (
            db.query(AgentPresenceEvent)
            .filter(AgentPresenceEvent.agent_id == agent_uuid)
            .filter(AgentPresenceEvent.ended_at.is_(None))
            .order_by(AgentPresenceEvent.started_at.desc())
            .first()
        )
        if current and current.status == new_status:
            return
        if current and current.ended_at is None:
            current.ended_at = now
        db.add(
            AgentPresenceEvent(
                agent_id=agent_uuid,
                status=new_status,
                started_at=now,
                ended_at=None,
                source=(source or "auto"),
            )
        )

    @staticmethod
    def get_or_create(db: Session, agent_id: str) -> AgentPresence:
        agent_uuid = coerce_uuid(agent_id)
        agent = db.get(CrmAgent, agent_uuid)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        presence = db.query(AgentPresence).filter(AgentPresence.agent_id == agent_uuid).first()
        if presence:
            return presence

        presence = AgentPresence(
            agent_id=agent_uuid,
            status=AgentPresenceStatus.offline,
            last_seen_at=None,
            manual_override_status=None,
            manual_override_set_at=None,
        )
        db.add(presence)
        db.commit()
        db.refresh(presence)
        return presence

    @staticmethod
    def upsert(
        db: Session,
        agent_id: str,
        *,
        status: AgentPresenceStatus | str | None = None,
        source: str = "auto",
    ) -> AgentPresence:
        agent_uuid = coerce_uuid(agent_id)
        agent = db.get(CrmAgent, agent_uuid)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        if status is None:
            status_value = AgentPresenceStatus.online
        else:
            status_value = validate_enum(status, AgentPresenceStatus, "status")

        now = AgentPresenceManager._now()
        presence = db.query(AgentPresence).filter(AgentPresence.agent_id == agent_uuid).first()

        if presence:
            presence.status = status_value
            presence.last_seen_at = now
        else:
            presence = AgentPresence(
                agent_id=agent_uuid,
                status=status_value,
                last_seen_at=now,
                manual_override_status=None,
                manual_override_set_at=None,
            )
            db.add(presence)

        effective = AgentPresenceManager._effective_status_for_row(presence)
        AgentPresenceManager._set_event_status(
            db,
            agent_uuid=agent_uuid,
            new_status=effective,
            source=source,
            now=now,
        )
        db.commit()
        db.refresh(presence)
        return presence

    @staticmethod
    def set_manual_override(
        db: Session,
        agent_id: str,
        *,
        status: AgentPresenceStatus | str,
    ) -> AgentPresence:
        agent_uuid = coerce_uuid(agent_id)
        presence = AgentPresenceManager.get_or_create(db, agent_id)
        status_value = validate_enum(status, AgentPresenceStatus, "status")
        if status_value not in {AgentPresenceStatus.on_break, AgentPresenceStatus.offline}:
            raise HTTPException(status_code=400, detail="Manual override only supports on_break or offline")

        now = AgentPresenceManager._now()
        # Manual status change is a user action; treat it as a fresh heartbeat so it takes effect immediately.
        presence.last_seen_at = now
        presence.manual_override_status = status_value
        presence.manual_override_set_at = now

        effective = AgentPresenceManager._effective_status_for_row(presence)
        AgentPresenceManager._set_event_status(
            db,
            agent_uuid=agent_uuid,
            new_status=effective,
            source="manual",
            now=now,
        )
        db.commit()
        db.refresh(presence)
        return presence

    @staticmethod
    def clear_manual_override(db: Session, agent_id: str) -> AgentPresence:
        agent_uuid = coerce_uuid(agent_id)
        presence = AgentPresenceManager.get_or_create(db, agent_id)
        now = AgentPresenceManager._now()
        # Clearing override is a user action; treat it as a fresh heartbeat.
        presence.last_seen_at = now
        presence.manual_override_status = None
        presence.manual_override_set_at = None

        effective = AgentPresenceManager._effective_status_for_row(presence)
        AgentPresenceManager._set_event_status(
            db,
            agent_uuid=agent_uuid,
            new_status=effective,
            source="manual",
            now=now,
        )
        db.commit()
        db.refresh(presence)
        return presence

    @staticmethod
    def list(
        db: Session,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AgentPresence]:
        query = db.query(AgentPresence)
        if status:
            status_value = validate_enum(status, AgentPresenceStatus, "status")
            query = query.filter(AgentPresence.status == status_value)
        return query.order_by(AgentPresence.updated_at.desc()).limit(limit).offset(offset).all()

    @staticmethod
    def effective_status(
        presence: AgentPresence,
        *,
        stale_after_minutes: int = DEFAULT_STALE_MINUTES,
    ) -> AgentPresenceStatus:
        return AgentPresenceManager._effective_status_for_row(
            presence,
            stale_after_minutes=stale_after_minutes,
        )

    @staticmethod
    def _normalize_captured_at(captured_at: datetime | None) -> datetime:
        now = AgentPresenceManager._now()
        if captured_at is None:
            return now
        parsed = captured_at
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        if parsed > now + timedelta(minutes=2):
            return now
        if parsed < now - timedelta(hours=24):
            return now
        return parsed

    @staticmethod
    def prune_location_pings(db: Session, *, retention_hours: int = DEFAULT_LOCATION_RETENTION_HOURS) -> int:
        cutoff = AgentPresenceManager._now() - timedelta(hours=max(int(retention_hours or 48), 1))
        deleted = (
            db.query(AgentLocationPing).filter(AgentLocationPing.received_at < cutoff).delete(synchronize_session=False)
        )
        return int(deleted or 0)

    @staticmethod
    def upsert_location(
        db: Session,
        agent_id: str,
        *,
        sharing_enabled: bool,
        latitude: float | None = None,
        longitude: float | None = None,
        accuracy_m: float | None = None,
        captured_at: datetime | None = None,
        status: AgentPresenceStatus | str | None = None,
        source: str = "browser",
    ) -> AgentPresence:
        lat: float | None = None
        lng: float | None = None
        normalized_accuracy: float | None = None

        if sharing_enabled:
            if latitude is None or longitude is None:
                raise HTTPException(
                    status_code=400, detail="Latitude and longitude are required when sharing is enabled"
                )
            lat = float(latitude)
            lng = float(longitude)
            if lat < -90 or lat > 90 or lng < -180 or lng > 180:
                raise HTTPException(status_code=400, detail="Invalid latitude/longitude range")
            normalized_accuracy = float(accuracy_m) if accuracy_m is not None else None

        presence = AgentPresenceManager.upsert(db, agent_id, status=status, source="auto")
        now = AgentPresenceManager._now()
        presence.location_sharing_enabled = bool(sharing_enabled)

        if sharing_enabled:
            assert lat is not None and lng is not None
            ping = AgentLocationPing(
                agent_id=coerce_uuid(agent_id),
                latitude=lat,
                longitude=lng,
                accuracy_m=normalized_accuracy,
                captured_at=AgentPresenceManager._normalize_captured_at(captured_at),
                source=(source or "browser")[:32],
            )
            db.add(ping)
            presence.last_latitude = lat
            presence.last_longitude = lng
            presence.last_location_accuracy_m = ping.accuracy_m
            presence.last_location_at = ping.captured_at
            presence.updated_at = now

        global _last_prune_at
        now = AgentPresenceManager._now()
        if _last_prune_at is None or (now - _last_prune_at).total_seconds() >= _PRUNE_INTERVAL_SECONDS:
            AgentPresenceManager.prune_location_pings(db)
            _last_prune_at = now

        db.commit()
        db.refresh(presence)
        return presence

    @staticmethod
    def list_live_locations(
        db: Session,
        *,
        stale_after_seconds: int = DEFAULT_LOCATION_STALE_SECONDS,
        limit: int = 200,
    ) -> builtins.list[dict]:
        stale_cutoff = AgentPresenceManager._now() - timedelta(seconds=max(int(stale_after_seconds or 120), 30))
        safe_limit = max(min(int(limit or 200), 500), 1)
        rows = (
            db.query(AgentPresence, CrmAgent)
            .join(CrmAgent, CrmAgent.id == AgentPresence.agent_id)
            .filter(CrmAgent.is_active.is_(True))
            .filter(AgentPresence.location_sharing_enabled.is_(True))
            .filter(AgentPresence.last_location_at.isnot(None))
            .filter(AgentPresence.last_location_at >= stale_cutoff)
            .order_by(AgentPresence.last_location_at.desc())
            .limit(safe_limit)
            .all()
        )
        from app.services.crm.teams.service import get_agent_labels

        agents = [agent for _, agent in rows]
        labels = get_agent_labels(db, agents)
        items: list[dict] = []
        for presence, agent in rows:
            effective = AgentPresenceManager._effective_status_for_row(
                presence,
                stale_after_minutes=max(int(stale_after_seconds / 60), 1),
            )
            if presence.last_latitude is None or presence.last_longitude is None or presence.last_location_at is None:
                continue
            items.append(
                {
                    "agent_id": str(agent.id),
                    "agent_label": labels.get(str(agent.id), "Agent"),
                    "status": presence.status.value,
                    "effective_status": effective.value,
                    "last_seen_at": presence.last_seen_at,
                    "latitude": float(presence.last_latitude),
                    "longitude": float(presence.last_longitude),
                    "accuracy_m": (
                        float(presence.last_location_accuracy_m)
                        if presence.last_location_accuracy_m is not None
                        else None
                    ),
                    "location_at": presence.last_location_at,
                }
            )
        return items

    @staticmethod
    def seconds_by_status(
        db: Session,
        *,
        agent_id: str,
        start_at: datetime,
        end_at: datetime,
    ) -> dict[str, float]:
        """Return raw seconds by status over [start_at, end_at] for one agent.

        Uses crm_agent_presence_events. Events are clipped to [start_at, end_at].
        """
        start = start_at
        end = end_at
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)
        if end <= start:
            return {s.value: 0.0 for s in AgentPresenceStatus}

        agent_uuid = coerce_uuid(agent_id)
        rows = (
            db.query(AgentPresenceEvent)
            .filter(AgentPresenceEvent.agent_id == agent_uuid)
            .filter(AgentPresenceEvent.started_at < end)
            .filter(func.coalesce(AgentPresenceEvent.ended_at, end) > start)
            .order_by(AgentPresenceEvent.started_at.asc())
            .all()
        )

        seconds: dict[str, float] = {s.value: 0.0 for s in AgentPresenceStatus}
        for ev in rows:
            ev_start = ev.started_at
            ev_end = ev.ended_at or end
            if ev_start.tzinfo is None:
                ev_start = ev_start.replace(tzinfo=UTC)
            if ev_end.tzinfo is None:
                ev_end = ev_end.replace(tzinfo=UTC)
            overlap_start = max(start, ev_start)
            overlap_end = min(end, ev_end)
            if overlap_end <= overlap_start:
                continue
            seconds[ev.status.value] = seconds.get(ev.status.value, 0.0) + (overlap_end - overlap_start).total_seconds()
        return seconds

    @staticmethod
    def seconds_by_status_bulk(
        db: Session,
        *,
        agent_ids: builtins.list[str],
        start_at: datetime,
        end_at: datetime,
    ) -> dict[str, dict[str, float]]:
        """Return raw seconds by status over [start_at, end_at] for many agents.

        This is the bulk version of seconds_by_status() and is designed for reports.
        Events are clipped to [start_at, end_at].
        """
        start = start_at
        end = end_at
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)
        if end <= start:
            return {}

        agent_uuids = [coerce_uuid(aid) for aid in agent_ids if aid]
        if not agent_uuids:
            return {}

        # Clip event intervals to [start, end] in SQL and sum overlap seconds.
        ev_end = func.coalesce(AgentPresenceEvent.ended_at, end)
        overlap_start = func.greatest(AgentPresenceEvent.started_at, start)
        overlap_end = func.least(ev_end, end)
        seconds_expr = func.greatest(func.extract("epoch", overlap_end - overlap_start), 0.0)

        rows = (
            db.query(
                AgentPresenceEvent.agent_id.label("agent_id"),
                AgentPresenceEvent.status.label("status"),
                func.sum(seconds_expr).label("seconds"),
            )
            .filter(AgentPresenceEvent.agent_id.in_(agent_uuids))
            .filter(AgentPresenceEvent.started_at < end)
            .filter(func.coalesce(AgentPresenceEvent.ended_at, end) > start)
            .group_by(AgentPresenceEvent.agent_id, AgentPresenceEvent.status)
            .all()
        )

        out: dict[str, dict[str, float]] = {}
        for agent_uuid, status, seconds in rows:
            agent_key = str(agent_uuid)
            status_key = status.value if hasattr(status, "value") else str(status)
            out.setdefault(agent_key, {})[status_key] = float(seconds or 0.0)
        return out


agent_presence = AgentPresenceManager()
