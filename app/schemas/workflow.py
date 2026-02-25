from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.workflow import SlaBreachStatus, SlaClockStatus, TicketAssignmentStrategy, WorkflowEntityType


class StatusTransitionBase(BaseModel):
    from_status: str = Field(min_length=1, max_length=40)
    to_status: str = Field(min_length=1, max_length=40)
    requires_note: bool = False
    is_active: bool = True


class TicketStatusTransitionCreate(StatusTransitionBase):
    pass


class TicketStatusTransitionUpdate(BaseModel):
    from_status: str | None = Field(default=None, min_length=1, max_length=40)
    to_status: str | None = Field(default=None, min_length=1, max_length=40)
    requires_note: bool | None = None
    is_active: bool | None = None


class TicketStatusTransitionRead(StatusTransitionBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class WorkOrderStatusTransitionCreate(StatusTransitionBase):
    pass


class WorkOrderStatusTransitionUpdate(BaseModel):
    from_status: str | None = Field(default=None, min_length=1, max_length=40)
    to_status: str | None = Field(default=None, min_length=1, max_length=40)
    requires_note: bool | None = None
    is_active: bool | None = None


class WorkOrderStatusTransitionRead(StatusTransitionBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class ProjectTaskStatusTransitionCreate(StatusTransitionBase):
    pass


class ProjectTaskStatusTransitionUpdate(BaseModel):
    from_status: str | None = Field(default=None, min_length=1, max_length=40)
    to_status: str | None = Field(default=None, min_length=1, max_length=40)
    requires_note: bool | None = None
    is_active: bool | None = None


class ProjectTaskStatusTransitionRead(StatusTransitionBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class SlaPolicyBase(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    entity_type: WorkflowEntityType
    description: str | None = None
    is_active: bool = True


class SlaPolicyCreate(SlaPolicyBase):
    pass


class SlaPolicyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    entity_type: WorkflowEntityType | None = None
    description: str | None = None
    is_active: bool | None = None


class SlaPolicyRead(SlaPolicyBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class SlaTargetBase(BaseModel):
    policy_id: UUID
    priority: str | None = Field(default=None, max_length=40)
    target_minutes: int = Field(ge=1)
    warning_minutes: int | None = Field(default=None, ge=1)
    is_active: bool = True


class SlaTargetCreate(SlaTargetBase):
    pass


class SlaTargetUpdate(BaseModel):
    policy_id: UUID | None = None
    priority: str | None = Field(default=None, max_length=40)
    target_minutes: int | None = Field(default=None, ge=1)
    warning_minutes: int | None = Field(default=None, ge=1)
    is_active: bool | None = None


class SlaTargetRead(SlaTargetBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class SlaClockBase(BaseModel):
    policy_id: UUID
    entity_type: WorkflowEntityType
    entity_id: UUID
    priority: str | None = Field(default=None, max_length=40)
    status: SlaClockStatus = SlaClockStatus.running
    started_at: datetime
    paused_at: datetime | None = None
    total_paused_seconds: int = Field(default=0, ge=0)
    due_at: datetime
    completed_at: datetime | None = None
    breached_at: datetime | None = None


class SlaClockCreate(BaseModel):
    policy_id: UUID
    entity_type: WorkflowEntityType
    entity_id: UUID
    priority: str | None = Field(default=None, max_length=40)
    started_at: datetime | None = None


class SlaClockUpdate(BaseModel):
    status: SlaClockStatus | None = None
    paused_at: datetime | None = None
    total_paused_seconds: int | None = Field(default=None, ge=0)
    due_at: datetime | None = None
    completed_at: datetime | None = None
    breached_at: datetime | None = None


class SlaClockRead(SlaClockBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class SlaBreachBase(BaseModel):
    clock_id: UUID
    status: SlaBreachStatus = SlaBreachStatus.open
    breached_at: datetime
    notes: str | None = None


class SlaBreachCreate(BaseModel):
    clock_id: UUID
    breached_at: datetime | None = None
    notes: str | None = None


class SlaBreachUpdate(BaseModel):
    status: SlaBreachStatus | None = None
    notes: str | None = None


class SlaBreachRead(SlaBreachBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class SlaReportBucket(BaseModel):
    key: str
    total: int
    breached: int
    breach_rate: float


class SlaReportSummary(BaseModel):
    total_clocks: int
    total_breaches: int
    breach_rate: float
    by_entity_type: list[SlaReportBucket]
    by_status: list[SlaReportBucket]
    ticket_by_service_team: list[SlaReportBucket]
    ticket_by_assignee: list[SlaReportBucket]


class SlaTrendPoint(BaseModel):
    date: str
    total: int
    breached: int
    breach_rate: float


class SlaTrendResponse(BaseModel):
    points: list[SlaTrendPoint]


class StatusTransitionRequest(BaseModel):
    to_status: str = Field(min_length=1, max_length=40)
    note: str | None = None


class TicketAssignmentRuleBase(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    priority: int = 0
    is_active: bool = True
    match_config: dict | None = None
    strategy: TicketAssignmentStrategy = TicketAssignmentStrategy.round_robin
    team_id: UUID | None = None
    assign_manager: bool = False
    assign_spc: bool = False


class TicketAssignmentRuleCreate(TicketAssignmentRuleBase):
    pass


class TicketAssignmentRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    priority: int | None = None
    is_active: bool | None = None
    match_config: dict | None = None
    strategy: TicketAssignmentStrategy | None = None
    team_id: UUID | None = None
    assign_manager: bool | None = None
    assign_spc: bool | None = None


class TicketAssignmentRuleRead(TicketAssignmentRuleBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class TicketAssignmentRuleReorderRequest(BaseModel):
    rule_ids: list[UUID] = Field(default_factory=list)


class TicketAssignmentRuleTestRequest(BaseModel):
    ticket_ref: str = Field(min_length=1, max_length=120)


class TicketAssignmentRuleTestResult(BaseModel):
    rule_id: UUID
    ticket_id: UUID
    matched: bool
    strategy: TicketAssignmentStrategy
    candidate_count: int = 0
    candidate_person_ids: list[str] = Field(default_factory=list)
    preview_assignee_person_id: str | None = None
    reason: str
