"""Agent presence tracking for CRM."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.crm.enums import AgentPresenceStatus
from app.models.crm.presence import AgentPresence
from app.models.crm.team import CrmAgent
from app.services.common import coerce_uuid, validate_enum
from app.services.response import ListResponseMixin

DEFAULT_STALE_MINUTES = 5


class AgentPresenceManager(ListResponseMixin):
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
    ) -> AgentPresence:
        agent_uuid = coerce_uuid(agent_id)
        agent = db.get(CrmAgent, agent_uuid)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        if status is None:
            status_value = AgentPresenceStatus.online
        else:
            status_value = validate_enum(status, AgentPresenceStatus, "status")

        now = datetime.now(UTC)
        presence = db.query(AgentPresence).filter(AgentPresence.agent_id == agent_uuid).first()

        if presence:
            presence.status = status_value
            presence.last_seen_at = now
        else:
            presence = AgentPresence(
                agent_id=agent_uuid,
                status=status_value,
                last_seen_at=now,
            )
            db.add(presence)

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
        if not presence.last_seen_at:
            return AgentPresenceStatus.offline
        cutoff = datetime.now(UTC) - timedelta(minutes=stale_after_minutes)
        if presence.last_seen_at < cutoff:
            return AgentPresenceStatus.offline
        return presence.status


agent_presence = AgentPresenceManager()
