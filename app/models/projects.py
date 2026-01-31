import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ProjectStatus(enum.Enum):
    planned = "planned"
    active = "active"
    on_hold = "on_hold"
    completed = "completed"
    canceled = "canceled"


class ProjectPriority(enum.Enum):
    low = "low"
    normal = "normal"
    high = "high"
    urgent = "urgent"


class ProjectType(enum.Enum):
    cable_rerun = "cable_rerun"
    fiber_optics_relocation = "fiber_optics_relocation"
    radio_fiber_relocation = "radio_fiber_relocation"
    fiber_optics_installation = "fiber_optics_installation"
    radio_installation = "radio_installation"


class TaskStatus(enum.Enum):
    backlog = "backlog"
    todo = "todo"
    in_progress = "in_progress"
    blocked = "blocked"
    done = "done"
    canceled = "canceled"


class TaskPriority(enum.Enum):
    low = "low"
    normal = "normal"
    high = "high"
    urgent = "urgent"


class TaskDependencyType(enum.Enum):
    finish_to_start = "finish_to_start"
    start_to_start = "start_to_start"
    finish_to_finish = "finish_to_finish"
    start_to_finish = "start_to_finish"


class ProjectTemplate(Base):
    __tablename__ = "project_templates"
    __table_args__ = (
        UniqueConstraint("project_type", name="uq_project_templates_project_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    project_type: Mapped[ProjectType | None] = mapped_column(Enum(ProjectType))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    tasks = relationship("ProjectTemplateTask", back_populates="template")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    code: Mapped[str | None] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)
    project_type: Mapped[ProjectType | None] = mapped_column(Enum(ProjectType))
    project_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_templates.id")
    )
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus), default=ProjectStatus.planned
    )
    priority: Mapped[ProjectPriority] = mapped_column(
        Enum(ProjectPriority), default=ProjectPriority.normal
    )
    subscriber_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subscribers.id")
    )
    created_by_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id")
    )
    owner_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id")
    )
    manager_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id")
    )
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tags: Mapped[list | None] = mapped_column(JSON)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    subscriber = relationship("Subscriber", back_populates="projects")
    created_by = relationship("Person", foreign_keys=[created_by_person_id])
    owner = relationship("Person", foreign_keys=[owner_person_id])
    manager = relationship("Person", foreign_keys=[manager_person_id])
    project_template = relationship("ProjectTemplate")
    tasks = relationship("ProjectTask", back_populates="project")
    comments = relationship("ProjectComment", back_populates="project")


class ProjectTask(Base):
    __tablename__ = "project_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_tasks.id")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    template_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_template_tasks.id")
    )
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="project_taskstatus"), default=TaskStatus.todo
    )
    priority: Mapped[TaskPriority] = mapped_column(
        Enum(TaskPriority), default=TaskPriority.normal
    )
    assigned_to_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id")
    )
    created_by_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id")
    )
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tickets.id")
    )
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_orders.id")
    )
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effort_hours: Mapped[int | None] = mapped_column(Integer)
    tags: Mapped[list | None] = mapped_column(JSON)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    project = relationship("Project", back_populates="tasks")
    parent_task = relationship("ProjectTask", remote_side=[id])
    assigned_to = relationship("Person", foreign_keys=[assigned_to_person_id])
    created_by = relationship("Person", foreign_keys=[created_by_person_id])
    template_task = relationship("ProjectTemplateTask")
    comments = relationship("ProjectTaskComment", back_populates="task")


class ProjectTemplateTask(Base):
    __tablename__ = "project_template_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_templates.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[TaskStatus | None] = mapped_column(
        Enum(TaskStatus, name="project_taskstatus")
    )
    priority: Mapped[TaskPriority | None] = mapped_column(Enum(TaskPriority, name="taskpriority"))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    template = relationship("ProjectTemplate", back_populates="tasks")


class ProjectTemplateTaskDependency(Base):
    __tablename__ = "project_template_task_dependency"
    __table_args__ = (
        UniqueConstraint(
            "template_task_id",
            "depends_on_template_task_id",
            name="uq_project_template_task_dependency",
        ),
        CheckConstraint(
            "template_task_id <> depends_on_template_task_id",
            name="ck_project_template_task_dependency_no_self",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    template_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_template_tasks.id"), nullable=False
    )
    depends_on_template_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_template_tasks.id"), nullable=False
    )
    dependency_type: Mapped[TaskDependencyType] = mapped_column(
        Enum(TaskDependencyType, name="taskdependencytype"),
        default=TaskDependencyType.finish_to_start,
    )
    lag_days: Mapped[int] = mapped_column(Integer, default=0)

    template_task = relationship(
        "ProjectTemplateTask",
        foreign_keys=[template_task_id],
    )
    depends_on_template_task = relationship(
        "ProjectTemplateTask",
        foreign_keys=[depends_on_template_task_id],
    )


class ProjectTaskDependency(Base):
    __tablename__ = "project_task_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "depends_on_task_id",
            name="uq_project_task_dependencies",
        ),
        CheckConstraint(
            "task_id <> depends_on_task_id",
            name="ck_project_task_dependencies_no_self",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_tasks.id"), nullable=False
    )
    depends_on_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_tasks.id"), nullable=False
    )
    dependency_type: Mapped[TaskDependencyType] = mapped_column(
        Enum(TaskDependencyType, name="taskdependencytype"),
        default=TaskDependencyType.finish_to_start,
    )
    lag_days: Mapped[int] = mapped_column(Integer, default=0)

    task = relationship("ProjectTask", foreign_keys=[task_id])
    depends_on_task = relationship("ProjectTask", foreign_keys=[depends_on_task_id])


class ProjectTaskComment(Base):
    __tablename__ = "project_task_comments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_tasks.id"), nullable=False
    )
    author_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id")
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    attachments: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    task = relationship("ProjectTask", back_populates="comments")
    author = relationship("Person")


class ProjectComment(Base):
    __tablename__ = "project_comments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    author_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id")
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    attachments: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    project = relationship("Project", back_populates="comments")
    author = relationship("Person")
