"""Schemas for the public "Track My Visit" JSON API."""

from pydantic import BaseModel, Field


class TrackRescheduleRequest(BaseModel):
    preferred_window: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=500)
