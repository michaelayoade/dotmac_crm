from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.csrf import get_csrf_token
from app.db import SessionLocal
from app.services.auth_dependencies import require_permission
from app.services.performance.reports import performance_reports
from app.services.performance.reviews import performance_reviews
from app.web.admin import get_current_user, get_sidebar_stats

router = APIRouter(prefix="/performance", tags=["web-admin-performance"])
templates = Jinja2Templates(directory="templates")


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ctx(request: Request, db: Session, **kwargs):
    user = get_current_user(request)
    return {
        "request": request,
        "user": user,
        "current_user": user,
        "sidebar_stats": get_sidebar_stats(db),
        "csrf_token": get_csrf_token(request),
        "active_menu": "reports",
        **kwargs,
    }


@router.get("", response_class=HTMLResponse, dependencies=[Depends(require_permission("reports:operations"))])
def performance_root(request: Request):
    return RedirectResponse(url="/admin/performance/team", status_code=303)


@router.get("/team", response_class=HTMLResponse, dependencies=[Depends(require_permission("reports:operations"))])
def team_overview(request: Request, db: Session = Depends(_get_db)):
    user = get_current_user(request)
    scope = performance_reports.build_access_scope(
        db, user["person_id"], user.get("roles", []), user.get("permissions", [])
    )
    rows = performance_reports.leaderboard_for_scope(db, scope)
    return templates.TemplateResponse(
        "admin/performance/team_overview.html",
        _ctx(request, db, active_page="team-performance", rows=rows),
    )


@router.get(
    "/team/_table", response_class=HTMLResponse, dependencies=[Depends(require_permission("reports:operations"))]
)
def team_table(request: Request, db: Session = Depends(_get_db)):
    user = get_current_user(request)
    scope = performance_reports.build_access_scope(
        db, user["person_id"], user.get("roles", []), user.get("permissions", [])
    )
    rows = performance_reports.leaderboard_for_scope(db, scope)
    return templates.TemplateResponse(
        "admin/performance/_leaderboard_table.html",
        _ctx(request, db, rows=rows),
    )


@router.get(
    "/agents/{person_id}", response_class=HTMLResponse, dependencies=[Depends(require_permission("reports:operations"))]
)
def agent_detail(request: Request, person_id: str, db: Session = Depends(_get_db)):
    user = get_current_user(request)
    scope = performance_reports.build_access_scope(
        db, user["person_id"], user.get("roles", []), user.get("permissions", [])
    )
    try:
        performance_reports.assert_can_access_person(scope, person_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    history = performance_reports.score_history(db, person_id)
    reviews = performance_reports.reviews(db, person_id, limit=10)
    return templates.TemplateResponse(
        "admin/performance/agent_detail.html",
        _ctx(request, db, active_page="team-performance", person_id=person_id, history=history, reviews=reviews),
    )


@router.get(
    "/agents/{person_id}/_scores",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("reports:operations"))],
)
def agent_scores_partial(request: Request, person_id: str, db: Session = Depends(_get_db)):
    history = performance_reports.score_history(db, person_id)
    latest = history[-1] if history else None
    return templates.TemplateResponse(
        "admin/performance/_score_cards.html",
        _ctx(request, db, person_id=person_id, latest=latest),
    )


@router.get(
    "/reviews/{review_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("reports:operations"))],
)
def review_detail(request: Request, review_id: str, db: Session = Depends(_get_db)):
    user = get_current_user(request)
    scope = performance_reports.build_access_scope(
        db, user["person_id"], user.get("roles", []), user.get("permissions", [])
    )
    try:
        review = performance_reports.review_detail_for_scope(db, scope, review_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return templates.TemplateResponse(
        "admin/performance/review_detail.html",
        _ctx(request, db, active_page="team-performance", review=review),
    )


@router.post(
    "/agents/{person_id}/generate-review",
    dependencies=[Depends(require_permission("reports:operations"))],
)
def generate_review(request: Request, person_id: str, db: Session = Depends(_get_db)):
    user = get_current_user(request)
    scope = performance_reports.build_access_scope(
        db, user["person_id"], user.get("roles", []), user.get("permissions", [])
    )
    try:
        performance_reports.assert_can_access_person(scope, person_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    history = performance_reports.score_history(db, person_id)
    if not history:
        raise HTTPException(status_code=400, detail="No score history for agent")
    latest = history[-1]
    try:
        performance_reviews.generate_manual_for_manager(
            db,
            requester_id=user["person_id"],
            requester_roles=user.get("roles", []),
            target_person_id=person_id,
            period_start=latest.score_period_start,
            period_end=latest.score_period_end,
            request=request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/admin/performance/agents/{person_id}", status_code=303)
