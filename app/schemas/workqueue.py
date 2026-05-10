"""Request schemas for Workqueue actions."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, model_validator

from app.services.workqueue.types import ItemKind


class SnoozeRequest(BaseModel):
    kind: ItemKind
    item_id: UUID
    until: datetime | None = None
    until_next_reply: bool = False
    preset: Literal["1h", "tomorrow", "next_week", "next_reply"] | None = None

    @model_validator(mode="after")
    def _exactly_one(self):
        if self.preset is None and self.until is None and not self.until_next_reply:
            raise ValueError("Provide preset, until, or until_next_reply")
        return self


class ItemRef(BaseModel):
    kind: ItemKind
    item_id: UUID
