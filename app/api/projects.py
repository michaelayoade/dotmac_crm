from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.projects import (
    ProjectCreate,
    ProjectRead,
    ProjectTaskCreate,
    ProjectTaskRead,
    ProjectTaskUpdate,
    ProjectUpdate,
)
from app.schemas.timecost import CostSummary
from app.services import projects as projects_service
from app.services import timecost as timecost_service

router = APIRouter()


@router.post(
    "/projects",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
    tags=["projects"],
)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)):
    return projects_service.projects.create(db, payload)


@router.get("/projects", response_model=ListResponse[ProjectRead], tags=["projects"])
def list_projects(
    subscriber_id: str | None = None,
    status: str | None = None,
    project_type: str | None = None,
    priority: str | None = None,
    owner_person_id: str | None = None,
    manager_person_id: str | None = None,
    project_manager_person_id: str | None = None,
    assistant_manager_person_id: str | None = None,
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return projects_service.projects.list_response(
        db,
        subscriber_id,
        status,
        project_type,
        priority,
        owner_person_id,
        manager_person_id,
        project_manager_person_id,
        assistant_manager_person_id,
        is_active,
        order_by,
        order_dir,
        limit,
        offset,
    )


@router.patch("/projects/{project_id}", response_model=ProjectRead, tags=["projects"])
def update_project(project_id: str, payload: ProjectUpdate, db: Session = Depends(get_db)):
    return projects_service.projects.update(db, project_id, payload)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["projects"])
def delete_project(project_id: str, db: Session = Depends(get_db)):
    projects_service.projects.delete(db, project_id)


@router.get("/projects/charts/summary", tags=["projects"])
def projects_chart_summary(db: Session = Depends(get_db)):
    return projects_service.projects.chart_summary(db)


@router.get("/projects/kanban", tags=["projects"])
def projects_kanban(db: Session = Depends(get_db)):
    return projects_service.projects.kanban_view(db)


@router.get("/projects/gantt", tags=["projects"])
def projects_gantt(db: Session = Depends(get_db)):
    return projects_service.projects.gantt_view(db)


class ProjectGanttUpdate(BaseModel):
    id: str
    field: str
    value: str


@router.post("/projects/gantt/due-date", tags=["projects"])
def projects_gantt_due_date(payload: ProjectGanttUpdate, db: Session = Depends(get_db)):
    return projects_service.projects.update_gantt_date(db, payload.id, payload.field, payload.value)


class ProjectKanbanMove(BaseModel):
    id: str
    from_: str | None = Field(default=None, alias="from")
    to: str
    position: int | None = None

    model_config = ConfigDict(populate_by_name=True)


@router.post("/projects/kanban/move", tags=["projects"])
def projects_kanban_move(payload: ProjectKanbanMove, db: Session = Depends(get_db)):
    return projects_service.projects.update_status(db, payload.id, payload.to)


@router.get("/projects/{project_id}", response_model=ProjectRead, tags=["projects"])
def get_project(project_id: str, db: Session = Depends(get_db)):
    return projects_service.projects.get(db, project_id)


@router.get(
    "/projects/{project_id}/cost-summary",
    response_model=CostSummary,
    tags=["projects"],
)
def project_cost_summary(project_id: str, db: Session = Depends(get_db)):
    return timecost_service.project_cost_summary(db, project_id)


@router.post(
    "/project-tasks",
    response_model=ProjectTaskRead,
    status_code=status.HTTP_201_CREATED,
    tags=["project-tasks"],
)
def create_project_task(payload: ProjectTaskCreate, db: Session = Depends(get_db)):
    return projects_service.project_tasks.create(db, payload)


@router.get(
    "/project-tasks/{task_id}",
    response_model=ProjectTaskRead,
    tags=["project-tasks"],
)
def get_project_task(task_id: str, db: Session = Depends(get_db)):
    return projects_service.project_tasks.get(db, task_id)


@router.get(
    "/project-tasks",
    response_model=ListResponse[ProjectTaskRead],
    tags=["project-tasks"],
)
def list_project_tasks(
    project_id: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    assigned_to_person_id: str | None = None,
    parent_task_id: str | None = None,
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return projects_service.project_tasks.list_response(
        db,
        project_id,
        status,
        priority,
        assigned_to_person_id,
        parent_task_id,
        is_active,
        order_by,
        order_dir,
        limit,
        offset,
    )


@router.patch(
    "/project-tasks/{task_id}",
    response_model=ProjectTaskRead,
    tags=["project-tasks"],
)
def update_project_task(task_id: str, payload: ProjectTaskUpdate, db: Session = Depends(get_db)):
    return projects_service.project_tasks.update(db, task_id, payload)


@router.delete(
    "/project-tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["project-tasks"],
)
def delete_project_task(task_id: str, db: Session = Depends(get_db)):
    projects_service.project_tasks.delete(db, task_id)
