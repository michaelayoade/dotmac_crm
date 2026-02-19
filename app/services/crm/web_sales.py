"""Service helpers for CRM sales and pipeline web routes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.crm.sales import Pipeline, PipelineStage
from app.models.person import Person
from app.schemas.crm.sales import PipelineCreate, PipelineStageCreate, PipelineStageUpdate, PipelineUpdate
from app.services import crm as crm_service
from app.services.common import coerce_uuid

DEFAULT_PIPELINE_STAGES: list[dict[str, int | str]] = [
    {"name": "Lead Identified", "probability": 10},
    {"name": "Qualification Call Completed", "probability": 20},
    {"name": "Needs Assessment / Demo", "probability": 35},
    {"name": "Proposal Sent", "probability": 50},
    {"name": "Commercial Negotiation", "probability": 70},
    {"name": "Decision Pending", "probability": 85},
    {"name": "Closed Won", "probability": 100},
    {"name": "Closed Lost", "probability": 0},
]


def _as_bool(value: str | None) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def sales_dashboard_data(db: Session, *, pipeline_id: str | None, period_days: int) -> dict[str, Any]:
    from app.services.crm import reports as reports_service

    pipelines = crm_service.pipelines.list(
        db=db,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=100,
        offset=0,
    )

    start_at = datetime.now(UTC) - timedelta(days=period_days)
    end_at = datetime.now(UTC)

    metrics = reports_service.sales_pipeline_metrics(
        db,
        pipeline_id=pipeline_id,
        start_at=start_at,
        end_at=end_at,
        owner_agent_id=None,
    )
    forecast = reports_service.sales_forecast(
        db,
        pipeline_id=pipeline_id,
        months_ahead=6,
    )
    agent_performance = reports_service.agent_sales_performance(
        db,
        start_at=start_at,
        end_at=end_at,
        pipeline_id=pipeline_id,
    )
    recent_leads = crm_service.leads.list(
        db=db,
        pipeline_id=pipeline_id,
        stage_id=None,
        owner_agent_id=None,
        status=None,
        is_active=True,
        order_by="updated_at",
        order_dir="desc",
        limit=10,
        offset=0,
    )
    person_ids = [lead.person_id for lead in recent_leads if lead.person_id]
    persons = db.query(Person).filter(Person.id.in_(person_ids)).all() if person_ids else []
    person_map = {str(p.id): p for p in persons}

    return {
        "pipelines": pipelines,
        "selected_pipeline_id": pipeline_id or "",
        "selected_period_days": period_days,
        "metrics": metrics,
        "forecast": forecast,
        "agent_performance": agent_performance[:10],
        "recent_leads": recent_leads,
        "person_map": person_map,
    }


def sales_pipeline_data(db: Session, *, pipeline_id: str | None) -> dict[str, Any]:
    pipelines = crm_service.pipelines.list(
        db=db,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=100,
        offset=0,
    )
    selected_pipeline_id = pipeline_id
    if not selected_pipeline_id and pipelines:
        selected_pipeline_id = str(pipelines[0].id)
    return {
        "pipelines": pipelines,
        "selected_pipeline_id": selected_pipeline_id or "",
    }


def pipeline_new_data() -> dict[str, Any]:
    return {
        "pipeline": {"name": "", "is_active": True, "create_default_stages": True},
        "form_title": "New Pipeline",
        "submit_label": "Create Pipeline",
        "action_url": "/admin/crm/settings/pipelines",
        "error": None,
    }


def create_pipeline(
    db: Session,
    *,
    name: str | None,
    is_active: str | None,
    create_default_stages: str | None,
) -> str:
    pipeline_name = (name or "").strip()
    if not pipeline_name:
        raise ValueError("Pipeline name is required.")

    is_active_value = _as_bool(is_active) if is_active is not None else True
    create_stages = _as_bool(create_default_stages)

    payload = PipelineCreate(name=pipeline_name, is_active=is_active_value)
    pipeline = crm_service.pipelines.create(db=db, payload=payload)
    if create_stages:
        for index, stage in enumerate(DEFAULT_PIPELINE_STAGES):
            probability_value = stage.get("probability")
            default_probability = int(probability_value) if isinstance(probability_value, int | str) else 0
            stage_payload = PipelineStageCreate(
                pipeline_id=pipeline.id,
                name=str(stage["name"]),
                order_index=index,
                default_probability=default_probability,
                is_active=True,
            )
            crm_service.pipeline_stages.create(db=db, payload=stage_payload)
    return str(pipeline.id)


def pipeline_create_error_data(
    name: str | None, is_active: str | None, create_default_stages: str | None
) -> dict[str, Any]:
    return {
        "pipeline": {
            "name": (name or "").strip(),
            "is_active": _as_bool(is_active) if is_active is not None else True,
            "create_default_stages": _as_bool(create_default_stages),
        },
        "form_title": "New Pipeline",
        "submit_label": "Create Pipeline",
        "action_url": "/admin/crm/settings/pipelines",
    }


def pipeline_settings_data(db: Session, *, bulk_result: str, bulk_count: str) -> dict[str, Any]:
    pipelines = db.query(Pipeline).order_by(Pipeline.is_active.desc(), Pipeline.created_at.desc()).limit(200).all()
    stages = (
        db.query(PipelineStage)
        .order_by(PipelineStage.pipeline_id.asc(), PipelineStage.order_index.asc(), PipelineStage.created_at.asc())
        .limit(1000)
        .all()
    )
    stage_map: dict[str, list[PipelineStage]] = {}
    for stage in stages:
        stage_map.setdefault(str(stage.pipeline_id), []).append(stage)

    return {
        "pipelines": pipelines,
        "stage_map": stage_map,
        "bulk_result": bulk_result,
        "bulk_count": bulk_count,
        "default_pipeline_stages": DEFAULT_PIPELINE_STAGES,
    }


def pipeline_edit_data(db: Session, *, pipeline_id: str) -> dict[str, Any]:
    pipeline = crm_service.pipelines.get(db, pipeline_id)
    return {
        "pipeline": pipeline,
        "form_title": "Edit Pipeline",
        "submit_label": "Update Pipeline",
        "action_url": f"/admin/crm/settings/pipelines/{pipeline_id}",
        "error": None,
    }


def update_pipeline(db: Session, *, pipeline_id: str, name: str | None, is_active: str | None) -> None:
    payload = PipelineUpdate(
        name=(name or "").strip() or None,
        is_active=_as_bool(is_active) if is_active is not None else None,
    )
    crm_service.pipelines.update(db=db, pipeline_id=pipeline_id, payload=payload)


def pipeline_update_error_data(*, pipeline_id: str, name: str | None, is_active: str | None) -> dict[str, Any]:
    return {
        "pipeline": {
            "id": pipeline_id,
            "name": (name or "").strip(),
            "is_active": _as_bool(is_active) if is_active is not None else True,
            "create_default_stages": False,
        },
        "form_title": "Edit Pipeline",
        "submit_label": "Update Pipeline",
        "action_url": f"/admin/crm/settings/pipelines/{pipeline_id}",
    }


def delete_pipeline(db: Session, pipeline_id: str) -> None:
    crm_service.pipelines.delete(db, pipeline_id)


def create_pipeline_stage(
    db: Session, *, pipeline_id: str, name: str, order_index: int, default_probability: int
) -> None:
    payload = PipelineStageCreate(
        pipeline_id=coerce_uuid(pipeline_id),
        name=name.strip(),
        order_index=order_index,
        default_probability=default_probability,
        is_active=True,
    )
    crm_service.pipeline_stages.create(db=db, payload=payload)


def update_pipeline_stage(
    db: Session,
    *,
    stage_id: str,
    name: str,
    order_index: int,
    default_probability: int,
    is_active: str | None,
) -> None:
    payload = PipelineStageUpdate(
        name=name.strip(),
        order_index=order_index,
        default_probability=default_probability,
        is_active=_as_bool(is_active) if is_active is not None else False,
    )
    crm_service.pipeline_stages.update(db=db, stage_id=stage_id, payload=payload)


def disable_pipeline_stage(db: Session, *, stage_id: str) -> None:
    payload = PipelineStageUpdate(is_active=False)
    crm_service.pipeline_stages.update(db=db, stage_id=stage_id, payload=payload)


def bulk_assign_pipeline_leads(db: Session, *, pipeline_id: str, stage_id: str | None, scope: str) -> int:
    return crm_service.leads.bulk_assign_pipeline(
        db,
        pipeline_id=pipeline_id,
        stage_id=(stage_id or "").strip() or None,
        scope=scope,
    )


__all__ = [
    "bulk_assign_pipeline_leads",
    "create_pipeline",
    "create_pipeline_stage",
    "delete_pipeline",
    "disable_pipeline_stage",
    "pipeline_create_error_data",
    "pipeline_edit_data",
    "pipeline_new_data",
    "pipeline_settings_data",
    "pipeline_update_error_data",
    "sales_dashboard_data",
    "sales_pipeline_data",
    "update_pipeline",
    "update_pipeline_stage",
]
