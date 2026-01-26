from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WirelessMastBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    pop_site_id: UUID | None = None
    name: str = Field(min_length=1, max_length=160)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    height_m: float | None = Field(default=None, ge=0, le=300)
    structure_type: str | None = Field(default=None, max_length=80)
    owner: str | None = Field(default=None, max_length=160)
    status: str = Field(default="active", max_length=40)
    notes: str | None = None
    metadata_: dict | None = Field(
        default=None,
        serialization_alias="metadata",
    )
    is_active: bool = True


class WirelessMastCreate(WirelessMastBase):
    pass


class WirelessMastUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    pop_site_id: UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=160)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    height_m: float | None = Field(default=None, ge=0, le=300)
    structure_type: str | None = Field(default=None, max_length=80)
    owner: str | None = Field(default=None, max_length=160)
    status: str | None = Field(default=None, max_length=40)
    notes: str | None = None
    metadata_: dict | None = Field(
        default=None,
        serialization_alias="metadata",
    )
    is_active: bool | None = None


class WirelessMastRead(WirelessMastBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    created_at: datetime
    updated_at: datetime
