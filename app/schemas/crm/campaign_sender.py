from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class CampaignSenderBase(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    from_name: str | None = Field(default=None, max_length=160)
    from_email: EmailStr
    reply_to: EmailStr | None = None
    is_active: bool = True


class CampaignSenderCreate(CampaignSenderBase):
    pass


class CampaignSenderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    from_name: str | None = Field(default=None, max_length=160)
    from_email: EmailStr | None = None
    reply_to: EmailStr | None = None
    is_active: bool | None = None


class CampaignSenderRead(CampaignSenderBase):
    id: UUID
