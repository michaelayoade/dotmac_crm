from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.automation_rule import AutomationLogOutcome, AutomationRuleStatus


class AutomationActionType(enum.Enum):
    assign_conversation = "assign_conversation"
    set_field = "set_field"
    add_tag = "add_tag"
    send_notification = "send_notification"
    create_work_order = "create_work_order"
    emit_event = "emit_event"


class ConditionItem(BaseModel):
    field: str = Field(min_length=1)
    op: str = Field(min_length=1)
    value: Any = None


class ActionItem(BaseModel):
    action_type: AutomationActionType
    params: dict[str, Any] = Field(default_factory=dict)


class AutomationRuleBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    event_type: str = Field(min_length=1, max_length=100)
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(min_length=1)
    priority: int = Field(default=0, ge=0)
    stop_after_match: bool = False
    cooldown_seconds: int = Field(default=0, ge=0)


class AutomationRuleCreate(AutomationRuleBase):
    pass


class AutomationRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    event_type: str | None = Field(default=None, min_length=1, max_length=100)
    conditions: list[dict[str, Any]] | None = None
    actions: list[dict[str, Any]] | None = None
    priority: int | None = Field(default=None, ge=0)
    stop_after_match: bool | None = None
    cooldown_seconds: int | None = Field(default=None, ge=0)
    status: AutomationRuleStatus | None = None
    is_active: bool | None = None


class AutomationRuleRead(AutomationRuleBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    status: AutomationRuleStatus
    execution_count: int = 0
    last_triggered_at: datetime | None = None
    created_by_id: UUID | None = None
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


class AutomationRuleLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    rule_id: UUID
    event_id: UUID
    event_type: str
    outcome: AutomationLogOutcome
    actions_executed: list[dict[str, Any]] | None = None
    duration_ms: int | None = None
    error: str | None = None
    created_at: datetime
