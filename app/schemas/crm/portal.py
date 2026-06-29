"""Schemas for the customer Portal API (RFC #73)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PortalSessionMintRequest(BaseModel):
    """Trusted server-to-server mint request (sub backend asserts the subject)."""

    crm_subscriber_id: str = Field(..., description="CRM subscriber/org id to scope the token to")
    actor: str = Field(default="subscriber", description="'subscriber' or 'reseller'")
    scopes: list[str] = Field(default_factory=list, description="Granted portal scopes")


class PortalSessionMintResponse(BaseModel):
    portal_token: str
    expires_at: int = Field(..., description="Unix epoch seconds")
    api_base: str = Field(default="/api/v1/portal")


class PortalMeResponse(BaseModel):
    """Echoes the scoped subject behind the portal token (whoami)."""

    subject_id: str
    actor: str
    scopes: list[str]
