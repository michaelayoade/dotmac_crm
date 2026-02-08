from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.person import ChannelTypeEnum, PartyStatusEnum


class ContactBase(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    display_name: str | None = Field(default=None, max_length=160)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=40)
    address_line1: str | None = Field(default=None, max_length=120)
    address_line2: str | None = Field(default=None, max_length=120)
    city: str | None = Field(default=None, max_length=80)
    region: str | None = Field(default=None, max_length=80)
    postal_code: str | None = Field(default=None, max_length=20)
    country_code: str | None = Field(default=None, max_length=2)
    organization_id: UUID | None = None
    party_status: PartyStatusEnum = PartyStatusEnum.contact
    is_active: bool = True
    notes: str | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class ContactCreate(ContactBase):
    pass


class ContactUpdate(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    display_name: str | None = Field(default=None, max_length=160)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    address_line1: str | None = Field(default=None, max_length=120)
    address_line2: str | None = Field(default=None, max_length=120)
    city: str | None = Field(default=None, max_length=80)
    region: str | None = Field(default=None, max_length=80)
    postal_code: str | None = Field(default=None, max_length=20)
    country_code: str | None = Field(default=None, max_length=2)
    organization_id: UUID | None = None
    party_status: PartyStatusEnum | None = None
    is_active: bool | None = None
    notes: str | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class ContactRead(ContactBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class ContactChannelBase(BaseModel):
    person_id: UUID
    channel_type: ChannelTypeEnum
    address: str = Field(min_length=1, max_length=255)
    is_primary: bool = False
    is_verified: bool = False
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class ContactChannelCreate(ContactChannelBase):
    pass


class ContactChannelUpdate(BaseModel):
    channel_type: ChannelTypeEnum | None = None
    address: str | None = Field(default=None, min_length=1, max_length=255)
    is_primary: bool | None = None
    is_verified: bool | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class ContactChannelRead(ContactChannelBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    created_at: datetime
    updated_at: datetime
