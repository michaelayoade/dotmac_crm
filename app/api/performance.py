from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.schemas.performance import AgentPerformanceGoalCreate, AgentPerformanceGoalRead, AgentPerformanceGoalUpdate
from app.services.performance.goals import performance_goals
from app.services.performance.reports import performance_reports
from app.services.performance.reviews import performance_reviews

router = APIRouter(prefix="/performance", tags=["performance"])


def _parse_period(period: str | None) -> tuple[datetime | None, datetime | None]:
    if not period:
        return None, None
    now = datetime.now(UTC)
    if period == "last_week":
        return now - timedelta(days=7), now
    if period == "last_month":
        return now - timedelta(days=30), now
    return None, None


@router.get("/scores")
def score_history(
    person_id: str | None = Query(None),
    period: str | None = Query(None),
    db: Session = Depends(get_db),
    auth=Depends(get_current_user),
):
    scope = performance_reports.build_access_scope(
        db,
        auth["person_id"],
        auth.get("roles", []),
        auth.get("scopes", []) or auth.get("permissions", []),
    )
    start_at, _ = _parse_period(period)
    try:
        return performance_reports.scores_for_scope(db, scope, person_id, start_at=start_at, limit=52)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/reviews")
def review_list(
    person_id: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    auth=Depends(get_current_user),
):
    scope = performance_reports.build_access_scope(
        db,
        auth["person_id"],
        auth.get("roles", []),
        auth.get("scopes", []) or auth.get("permissions", []),
    )
    try:
        return performance_reports.reviews_for_scope(db, scope, person_id, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/reviews/generate")
def generate_review(
    person_id: str,
    period_start: datetime,
    period_end: datetime,
    db: Session = Depends(get_db),
    auth=Depends(get_current_user),
):
    scope = performance_reports.build_access_scope(
        db,
        auth["person_id"],
        auth.get("roles", []),
        auth.get("scopes", []) or auth.get("permissions", []),
    )
    try:
        performance_reports.assert_can_access_person(scope, person_id)
        return performance_reviews.generate_manual_for_manager(
            db,
            requester_id=auth["person_id"],
            requester_roles=auth.get("roles", []),
            target_person_id=person_id,
            period_start=period_start,
            period_end=period_end,
            request=None,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail == "Forbidden":
            raise HTTPException(status_code=403, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc


@router.get("/goals", response_model=list[AgentPerformanceGoalRead])
def list_goals(
    person_id: str | None = Query(None),
    db: Session = Depends(get_db),
    auth=Depends(get_current_user),
):
    scope = performance_reports.build_access_scope(
        db,
        auth["person_id"],
        auth.get("roles", []),
        auth.get("scopes", []) or auth.get("permissions", []),
    )
    try:
        effective_person_id = performance_reports.resolve_effective_person_id(scope, person_id)
        return performance_goals.list(db, effective_person_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/goals", response_model=AgentPerformanceGoalRead)
def create_goal(
    payload: AgentPerformanceGoalCreate,
    db: Session = Depends(get_db),
    auth=Depends(get_current_user),
):
    scope = performance_reports.build_access_scope(
        db,
        auth["person_id"],
        auth.get("roles", []),
        auth.get("scopes", []) or auth.get("permissions", []),
    )
    try:
        performance_reports.assert_can_access_person(scope, payload.person_id)
        return performance_goals.create(db, payload, created_by_person_id=auth.get("person_id"))
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.patch("/goals/{goal_id}", response_model=AgentPerformanceGoalRead)
def update_goal(
    goal_id: str,
    payload: AgentPerformanceGoalUpdate,
    db: Session = Depends(get_db),
    auth=Depends(get_current_user),
):
    goal = performance_goals.get(db, goal_id)
    scope = performance_reports.build_access_scope(
        db,
        auth["person_id"],
        auth.get("roles", []),
        auth.get("scopes", []) or auth.get("permissions", []),
    )
    try:
        performance_reports.assert_can_access_person(scope, str(goal.person_id))
        return performance_goals.update(db, goal_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/peer-comparison")
def peer_comparison(
    period: str | None = Query(None),
    db: Session = Depends(get_db),
    auth=Depends(get_current_user),
):
    _start_at, _end_at = _parse_period(period)
    scope = performance_reports.build_access_scope(
        db,
        auth["person_id"],
        auth.get("roles", []),
        auth.get("scopes", []) or auth.get("permissions", []),
    )
    return performance_reports.peer_comparison(db, scope)


@router.get("/team-summary")
def team_summary(
    team_id: str | None = Query(None),
    period: str | None = Query(None),
    db: Session = Depends(get_db),
    auth=Depends(get_current_user),
):
    scope = performance_reports.build_access_scope(
        db,
        auth["person_id"],
        auth.get("roles", []),
        auth.get("scopes", []) or auth.get("permissions", []),
    )
    return performance_reports.team_summary(db, scope, team_id=team_id, period=period)
