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
    location_sharing_enabled: bool = False
    last_latitude: float | None = None
    last_longitude: float | None = None
    last_location_accuracy_m: float | None = None
    last_location_at: datetime | None = None


class AgentLocationUpdate(BaseModel):
    sharing_enabled: bool = False
    status: AgentPresenceStatus | None = None
    latitude: float | None = None
    longitude: float | None = None
    accuracy_m: float | None = None
    captured_at: datetime | None = None


class AgentLiveLocationRead(BaseModel):
    agent_id: UUID
    agent_label: str | None = None
    status: AgentPresenceStatus
    effective_status: AgentPresenceStatus
    last_seen_at: datetime | None = None
    latitude: float
    longitude: float
    accuracy_m: float | None = None
    location_at: datetime
