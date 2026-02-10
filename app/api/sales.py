"""Sales CRM API endpoints."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.services.crm import reports as reports_service
from app.services.crm import sales as sales_service

router = APIRouter(prefix="/leads", tags=["sales"])


class KanbanMoveRequest(BaseModel):
    """Request body for moving a lead on the kanban board."""
    model_config = ConfigDict(populate_by_name=True)

    id: str
    to: str  # Target stage ID
    from_: str | None = Field(default=None, alias="from")  # Source stage ID (optional)
    position: int | None = None  # Position in the column (optional)


@router.get("/kanban")
def get_kanban_data(
    pipeline_id: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Get kanban board data for sales pipeline.

    Returns columns (stages) and records (leads) for rendering a kanban board.
    """
    return sales_service.Leads.kanban_view(db, pipeline_id)


@router.post("/kanban/move")
def move_kanban_card(
    request: KanbanMoveRequest,
    db: Session = Depends(get_db),
):
    """Move a lead to a different stage on the kanban board.

    Auto-updates probability from the target stage's default if not already set.
    """
    return sales_service.Leads.update_stage(db, request.id, request.to)


@router.get("/pipeline-summary")
def get_pipeline_summary(
    pipeline_id: str | None = Query(None),
    start_at: datetime | None = Query(None),
    end_at: datetime | None = Query(None),
    owner_agent_id: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Get sales pipeline metrics summary.

    Returns total pipeline value, weighted value, deal counts, win rate,
    average deal size, and stage breakdown.
    """
    return reports_service.sales_pipeline_metrics(
        db,
        pipeline_id=pipeline_id,
        start_at=start_at,
        end_at=end_at,
        owner_agent_id=owner_agent_id,
    )


@router.get("/forecast")
def get_sales_forecast(
    pipeline_id: str | None = Query(None),
    months_ahead: int = Query(6, ge=1, le=24),
    db: Session = Depends(get_db),
):
    """Get monthly sales forecast.

    Returns expected and weighted values by month based on expected_close_date.
    """
    return reports_service.sales_forecast(
        db,
        pipeline_id=pipeline_id,
        months_ahead=months_ahead,
    )


@router.get("/agent-performance")
def get_agent_performance(
    pipeline_id: str | None = Query(None),
    start_at: datetime | None = Query(None),
    end_at: datetime | None = Query(None),
    db: Session = Depends(get_db),
):
    """Get per-agent sales performance metrics.

    Returns deals won, deals lost, total value, and win rate for each agent.
    """
    return reports_service.agent_sales_performance(
        db,
        start_at=start_at,
        end_at=end_at,
        pipeline_id=pipeline_id,
    )
