from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.ai_insight import AIInsightRead, AnalyzeRequest
from app.services.ai.data_health import (
    ALERT_SNOOZE_HOURS_ALLOWED,
    acknowledge_risk_alerts,
    build_data_health_baseline_snapshot,
    compute_effective_risk_alerts,
    compute_risk_inventory_deltas,
    get_data_health_report,
    get_data_health_trend,
    get_latest_data_health_baseline_snapshot,
    get_previous_data_health_baseline_snapshot,
    persist_data_health_baseline_snapshot,
    snooze_risk_alerts,
)
from app.services.ai.engine import intelligence_engine
from app.services.ai.insights import ai_insights
from app.services.ai.personas import persona_registry
from app.services.ai.use_cases import suggest_conversation_reply, summarize_ticket
from app.services.auth_dependencies import require_permission, require_user_auth

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get(
    "/insights",
    dependencies=[Depends(require_permission("reports:operations"))],
)
def list_insights(
    domain: str | None = None,
    persona_key: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    items = ai_insights.list(
        db,
        domain=domain,
        persona_key=persona_key,
        entity_type=entity_type,
        entity_id=entity_id,
        status=status,
        severity=severity,
        limit=min(max(int(limit), 1), 100),
        offset=max(int(offset), 0),
    )
    return {"items": [AIInsightRead.model_validate(i) for i in items], "count": len(items)}


@router.get(
    "/insights/{insight_id}",
    dependencies=[Depends(require_permission("reports:operations"))],
)
def get_insight(insight_id: str, db: Session = Depends(get_db)):
    return AIInsightRead.model_validate(ai_insights.get(db, insight_id))


@router.post(
    "/insights/{insight_id}/acknowledge",
    dependencies=[Depends(require_permission("reports:operations"))],
)
def acknowledge_insight(
    insight_id: str,
    db: Session = Depends(get_db),
    auth=Depends(require_user_auth),
):
    person_id = str(auth.get("person_id")) if auth else None
    if not person_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    insight = ai_insights.acknowledge(db, insight_id, person_id)
    return {"id": str(insight.id), "status": insight.status.value}


@router.post(
    "/insights/{insight_id}/action",
    dependencies=[Depends(require_permission("reports:operations"))],
)
def action_insight(
    insight_id: str,
    db: Session = Depends(get_db),
    auth=Depends(require_user_auth),
):
    person_id = str(auth.get("person_id")) if auth else None
    insight = ai_insights.action(db, insight_id, person_id=person_id)
    return {"id": str(insight.id), "status": insight.status.value}


@router.post(
    "/insights/{insight_id}/expire",
    dependencies=[Depends(require_permission("reports:operations"))],
)
def expire_insight(
    insight_id: str,
    db: Session = Depends(get_db),
):
    insight = ai_insights.expire(db, insight_id)
    return {"id": str(insight.id), "status": insight.status.value}


@router.post(
    "/analyze/{persona_key}",
    dependencies=[Depends(require_permission("reports:operations"))],
)
def invoke_analysis(
    persona_key: str,
    payload: AnalyzeRequest,
    db: Session = Depends(get_db),
    auth=Depends(require_user_auth),
):
    try:
        insight = intelligence_engine.invoke(
            db,
            persona_key=persona_key,
            params=payload.params or {},
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            trigger="on_demand",
            triggered_by_person_id=str(auth.get("person_id")) if auth else None,
        )
        return {"insight_id": str(insight.id), "status": insight.status.value}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/analyze/{persona_key}/async",
    dependencies=[Depends(require_permission("reports:operations"))],
)
def invoke_analysis_async(
    persona_key: str,
    payload: AnalyzeRequest,
    auth=Depends(require_user_auth),
):
    from app.tasks.intelligence import invoke_persona_async

    # Validate persona key early for clearer errors.
    try:
        persona_registry.get(persona_key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        task = invoke_persona_async.delay(
            persona_key,
            params=payload.params or {},
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            trigger="on_demand",
            triggered_by_person_id=str(auth.get("person_id")) if auth else None,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Async queue unavailable") from exc
    return {"task_id": task.id, "persona_key": persona_key}


@router.get(
    "/data-health",
    dependencies=[Depends(require_permission("reports:operations"))],
)
def get_ai_data_health(
    sample_limit: int = 20,
    db: Session = Depends(get_db),
):
    return get_data_health_report(db, sample_limit=sample_limit)


@router.get(
    "/data-health/trend",
    dependencies=[Depends(require_permission("reports:operations"))],
)
def get_ai_data_health_trend(
    days: int = 14,
    persona_key: str | None = None,
    domain: str | None = None,
    db: Session = Depends(get_db),
):
    return get_data_health_trend(db, days=days, persona_key=persona_key, domain=domain)


@router.get(
    "/data-health/baseline/latest",
    dependencies=[Depends(require_permission("reports:operations"))],
)
def get_ai_data_health_baseline_latest(
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    snapshot = get_latest_data_health_baseline_snapshot(db)
    previous = get_previous_data_health_baseline_snapshot(db)
    return {
        "snapshot": snapshot,
        "previous_snapshot": previous,
        "risk_deltas": compute_risk_inventory_deltas(snapshot, previous),
        "risk_alerts": compute_effective_risk_alerts(
            db,
            latest_snapshot=snapshot,
            previous_snapshot=previous,
        ),
        "alert_state_actor": str(auth.get("person_id")) if auth else None,
    }


@router.post(
    "/data-health/baseline/capture",
    dependencies=[Depends(require_permission("reports:operations"))],
)
def capture_ai_data_health_baseline(
    sample_limit: int = 20,
    trend_days: int = 14,
    db: Session = Depends(get_db),
):
    snapshot = build_data_health_baseline_snapshot(db, sample_limit=sample_limit, trend_days=trend_days)
    persist_data_health_baseline_snapshot(db, snapshot)
    return {"snapshot": snapshot}


@router.post(
    "/data-health/baseline/alerts/acknowledge",
    dependencies=[Depends(require_permission("reports:operations"))],
)
def acknowledge_ai_data_health_alerts(
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    actor_person_id = str(auth.get("person_id")) if auth else None
    payload = acknowledge_risk_alerts(db, actor_person_id=actor_person_id)
    return {"ok": True, "acknowledged": payload}


@router.post(
    "/data-health/baseline/alerts/snooze",
    dependencies=[Depends(require_permission("reports:operations"))],
)
def snooze_ai_data_health_alerts(
    hours: int = 24,
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    if int(hours) not in ALERT_SNOOZE_HOURS_ALLOWED:
        raise HTTPException(status_code=400, detail="hours must be one of: 8, 24, 72")
    actor_person_id = str(auth.get("person_id")) if auth else None
    payload = snooze_risk_alerts(db, hours=hours, actor_person_id=actor_person_id)
    return {"ok": True, "snooze": payload}


@router.get(
    "/personas",
    dependencies=[Depends(require_permission("reports:operations"))],
)
def list_personas():
    specs = persona_registry.list_all()
    return {
        "personas": [
            {
                "key": s.key,
                "name": s.name,
                "domain": s.domain.value,
                "description": s.description,
                "supports_scheduled": bool(s.supports_scheduled),
                "default_endpoint": s.default_endpoint,
                "default_max_tokens": s.default_max_tokens,
                "setting_key": s.setting_key,
            }
            for s in specs
        ]
    }


@router.post("/crm/conversations/{conversation_id}/suggest-reply")
def ai_suggest_crm_reply(
    conversation_id: str,
    db: Session = Depends(get_db),
    auth=Depends(require_user_auth),
):
    try:
        result = suggest_conversation_reply(
            db,
            request=None,
            conversation_id=conversation_id,
            actor_person_id=str(auth.get("person_id")) if auth else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"draft": result.draft, "meta": result.meta}


@router.post("/tickets/{ticket_id}/summarize")
def ai_summarize_ticket(
    ticket_id: str,
    db: Session = Depends(get_db),
    auth=Depends(require_user_auth),
):
    try:
        result = summarize_ticket(
            db,
            request=None,
            ticket_id=ticket_id,
            actor_person_id=str(auth.get("person_id")) if auth else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"summary": result.summary, "next_actions": result.next_actions, "meta": result.meta}
