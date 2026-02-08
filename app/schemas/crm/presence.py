from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.crm.enums import AgentPresenceStatus


class AgentPresenceBase(BaseModel):
    agent_id: UUID
    status: AgentPresenceStatus
    last_seen_at: datetime | None = None


class AgentPresenceUpdate(BaseModel):
    status: AgentPresenceStatus | None = None


class AgentPresenceRead(AgentPresenceBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime
    effective_status: AgentPresenceStatus | None = None
