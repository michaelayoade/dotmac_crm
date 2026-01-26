from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.crm.enums import ChannelType


class TeamBase(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    is_active: bool = True
    notes: str | None = Field(default=None, max_length=255)
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class TeamCreate(TeamBase):
    pass


class TeamUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=160)
    is_active: bool | None = None
    notes: str | None = Field(default=None, max_length=255)
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class TeamRead(TeamBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class AgentBase(BaseModel):
    person_id: UUID
    is_active: bool = True
    title: str | None = Field(default=None, max_length=120)
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class AgentCreate(AgentBase):
    pass


class AgentUpdate(BaseModel):
    person_id: UUID | None = None
    is_active: bool | None = None
    title: str | None = Field(default=None, max_length=120)
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class AgentRead(AgentBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class AgentTeamBase(BaseModel):
    agent_id: UUID
    team_id: UUID
    is_active: bool = True


class AgentTeamCreate(AgentTeamBase):
    pass


class AgentTeamRead(AgentTeamBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime


class TeamChannelBase(BaseModel):
    team_id: UUID
    channel_type: ChannelType
    channel_target_id: UUID | None = None
    is_active: bool = True


class TeamChannelCreate(TeamChannelBase):
    pass


class TeamChannelRead(TeamChannelBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime


class RoutingRuleBase(BaseModel):
    team_id: UUID
    channel_type: ChannelType
    rule_config: dict | None = None
    is_active: bool = True


class RoutingRuleCreate(RoutingRuleBase):
    pass


class RoutingRuleUpdate(BaseModel):
    channel_type: ChannelType | None = None
    rule_config: dict | None = None
    is_active: bool | None = None


class RoutingRuleRead(RoutingRuleBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime
