from __future__ import annotations

from pydantic import BaseModel, Field


class CampaignSmtpBase(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=587, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=255)
    use_tls: bool = True
    use_ssl: bool = False
    is_active: bool = True


class CampaignSmtpCreate(CampaignSmtpBase):
    pass


class CampaignSmtpUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=255)
    use_tls: bool | None = None
    use_ssl: bool | None = None
    is_active: bool | None = None
