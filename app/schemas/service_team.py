from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.service_team import ServiceTeamMemberRole, ServiceTeamType


class ServiceTeamBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str = Field(min_length=1, max_length=160)
    team_type: ServiceTeamType
    region: str | None = Field(default=None, max_length=80)
    manager_person_id: UUID | None = None
    erp_department: str | None = Field(default=None, max_length=120)
    is_active: bool = True
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class ServiceTeamCreate(ServiceTeamBase):
    pass


class ServiceTeamUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    team_type: ServiceTeamType | None = None
    region: str | None = Field(default=None, max_length=80)
    manager_person_id: UUID | None = None
    erp_department: str | None = Field(default=None, max_length=120)
    is_active: bool | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class ServiceTeamRead(ServiceTeamBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    id: UUID
    created_at: datetime
    updated_at: datetime


class ServiceTeamMemberBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    team_id: UUID
    person_id: UUID
    role: ServiceTeamMemberRole = ServiceTeamMemberRole.member
    is_active: bool = True


class ServiceTeamMemberCreate(BaseModel):
    person_id: UUID
    role: ServiceTeamMemberRole = ServiceTeamMemberRole.member


class ServiceTeamMemberUpdate(BaseModel):
    role: ServiceTeamMemberRole | None = None
    is_active: bool | None = None


class ServiceTeamMemberRead(ServiceTeamMemberBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    id: UUID
    created_at: datetime
