from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.projects import ProjectPriority, ProjectStatus, ProjectType, TaskPriority, TaskStatus


class ProjectBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str = Field(min_length=1, max_length=160)
    code: str | None = Field(default=None, max_length=80)
    description: str | None = None
    customer_address: str | None = None
    project_type: ProjectType | None = None
    project_template_id: UUID | None = None
    status: ProjectStatus = ProjectStatus.planned
    priority: ProjectPriority = ProjectPriority.normal
    subscriber_id: UUID | None = None
    lead_id: UUID | None = None
    created_by_person_id: UUID | None = None
    owner_person_id: UUID | None = None
    manager_person_id: UUID | None = None
    project_manager_person_id: UUID | None = None
    assistant_manager_person_id: UUID | None = None
    start_at: datetime | None = None
    due_at: datetime | None = None
    completed_at: datetime | None = None
    region: str | None = Field(default=None, max_length=80)
    tags: list[str] | None = None
    metadata_: dict | None = Field(
        default=None,
        serialization_alias="metadata",
    )
    is_active: bool = True


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    code: str | None = Field(default=None, max_length=80)
    description: str | None = None
    customer_address: str | None = None
    project_type: ProjectType | None = None
    project_template_id: UUID | None = None
    status: ProjectStatus | None = None
    priority: ProjectPriority | None = None
    subscriber_id: UUID | None = None
    lead_id: UUID | None = None
    created_by_person_id: UUID | None = None
    owner_person_id: UUID | None = None
    manager_person_id: UUID | None = None
    project_manager_person_id: UUID | None = None
    assistant_manager_person_id: UUID | None = None
    start_at: datetime | None = None
    due_at: datetime | None = None
    completed_at: datetime | None = None
    region: str | None = Field(default=None, max_length=80)
    tags: list[str] | None = None
    metadata_: dict | None = Field(
        default=None,
        serialization_alias="metadata",
    )
    is_active: bool | None = None

    @model_validator(mode="after")
    def _validate_dates(self) -> ProjectUpdate:
        if self.start_at and self.due_at:
            if self.start_at >= self.due_at:
                raise ValueError("start_at must be before due_at")
        return self


class ProjectRead(ProjectBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class ProjectTemplateBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str = Field(min_length=1, max_length=160)
    project_type: ProjectType | None = None
    description: str | None = None
    is_active: bool = True


class ProjectTemplateCreate(ProjectTemplateBase):
    pass


class ProjectTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    project_type: ProjectType | None = None
    description: str | None = None
    is_active: bool | None = None


class ProjectTemplateRead(ProjectTemplateBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class ProjectTemplateTaskBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    template_id: UUID
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    sort_order: int = 0
    effort_hours: int | None = None
    is_active: bool = True


class ProjectTemplateTaskCreate(ProjectTemplateTaskBase):
    pass


class ProjectTemplateTaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    sort_order: int | None = None
    effort_hours: int | None = None
    is_active: bool | None = None


class ProjectTemplateTaskRead(ProjectTemplateTaskBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class ProjectTaskCommentBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    task_id: UUID
    author_person_id: UUID | None = None
    body: str = Field(min_length=1)
    attachments: list[dict] | None = None


class ProjectTaskCommentCreate(ProjectTaskCommentBase):
    pass


class ProjectTaskCommentRead(ProjectTaskCommentBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    created_at: datetime


class ProjectCommentBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    project_id: UUID
    author_person_id: UUID | None = None
    body: str = Field(min_length=1)
    attachments: list[dict] | None = None


class ProjectCommentCreate(ProjectCommentBase):
    pass


class ProjectCommentUpdate(BaseModel):
    body: str | None = Field(default=None, min_length=1)
    attachments: list[dict] | None = None


class ProjectCommentRead(ProjectCommentBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    created_at: datetime


class ProjectTaskBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    project_id: UUID
    parent_task_id: UUID | None = None
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    status: TaskStatus = TaskStatus.todo
    priority: TaskPriority = TaskPriority.normal
    assigned_to_person_id: UUID | None = None
    created_by_person_id: UUID | None = None
    ticket_id: UUID | None = None
    work_order_id: UUID | None = None
    start_at: datetime | None = None
    due_at: datetime | None = None
    completed_at: datetime | None = None
    effort_hours: int | None = None
    tags: list[str] | None = None
    metadata_: dict | None = Field(
        default=None,
        serialization_alias="metadata",
    )
    is_active: bool = True


class ProjectTaskCreate(ProjectTaskBase):
    pass


class ProjectTaskUpdate(BaseModel):
    project_id: UUID | None = None
    parent_task_id: UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    assigned_to_person_id: UUID | None = None
    created_by_person_id: UUID | None = None
    ticket_id: UUID | None = None
    work_order_id: UUID | None = None
    start_at: datetime | None = None
    due_at: datetime | None = None
    completed_at: datetime | None = None
    effort_hours: int | None = None
    tags: list[str] | None = None
    metadata_: dict | None = Field(
        default=None,
        serialization_alias="metadata",
    )
    is_active: bool | None = None


class ProjectTaskRead(ProjectTaskBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    created_at: datetime
    updated_at: datetime
