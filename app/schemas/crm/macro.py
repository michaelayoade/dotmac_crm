from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.crm.enums import MacroVisibility


class MacroActionSchema(BaseModel):
    action_type: str
    params: dict[str, Any] = Field(default_factory=dict)


class MacroCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    visibility: MacroVisibility = MacroVisibility.personal
    actions: list[MacroActionSchema] = Field(min_length=1)


class MacroUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    visibility: MacroVisibility | None = None
    actions: list[MacroActionSchema] | None = None
    is_active: bool | None = None


class MacroRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    name: str
    description: str | None = None
    visibility: MacroVisibility
    created_by_agent_id: UUID
    actions: list[dict[str, Any]]
    execution_count: int = 0
    is_active: bool = True
    created_at: datetime
    updated_at: datetime
