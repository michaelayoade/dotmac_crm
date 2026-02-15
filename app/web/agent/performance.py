from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services.crm.inbox.permissions import can_view_inbox
from app.services.performance.reports import performance_reports
from app.services.performance.reviews import performance_reviews
from app.web.admin import get_current_user, get_sidebar_stats
from app.web.auth.dependencies import require_web_auth

router = APIRouter(prefix="/agent", tags=["web-agent-performance"], dependencies=[Depends(require_web_auth)])
templates = Jinja2Templates(directory="templates")


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/my-performance", response_class=HTMLResponse)
def my_scorecard(request: Request, db: Session = Depends(_get_db)):
    user = get_current_user(request)
    if not can_view_inbox(user.get("roles", []), user.get("permissions", [])):
        raise HTTPException(status_code=403, detail="Forbidden")
    history = performance_reports.score_history(db, user["person_id"])
    reviews = performance_reports.reviews(db, user["person_id"], limit=10)
    return templates.TemplateResponse(
        "agent/performance/my_scorecard.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "my-performance",
            "active_menu": "reports",
            "history": history,
            "reviews": reviews,
        },
    )


@router.get("/my-performance/_scores", response_class=HTMLResponse)
def my_scores_partial(request: Request, db: Session = Depends(_get_db)):
    user = get_current_user(request)
    if not can_view_inbox(user.get("roles", []), user.get("permissions", [])):
        raise HTTPException(status_code=403, detail="Forbidden")
    history = performance_reports.score_history(db, user["person_id"])
    latest = history[-1] if history else None
    return templates.TemplateResponse(
        "admin/performance/_score_cards.html",
        {
            "request": request,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "latest": latest,
            "person_id": user["person_id"],
        },
    )


@router.get("/my-performance/reviews/{review_id}", response_class=HTMLResponse)
def my_review_detail(request: Request, review_id: str, db: Session = Depends(_get_db)):
    user = get_current_user(request)
    if not can_view_inbox(user.get("roles", []), user.get("permissions", [])):
        raise HTTPException(status_code=403, detail="Forbidden")
    scope = performance_reports.build_access_scope(
        db, user["person_id"], user.get("roles", []), user.get("permissions", [])
    )
    try:
        review = performance_reports.review_detail_for_scope(db, scope, review_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if str(review.person_id) != user["person_id"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    return templates.TemplateResponse(
        "admin/performance/review_detail.html",
        {
            "request": request,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "review": review,
            "active_page": "my-performance",
            "active_menu": "reports",
            "is_self_view": True,
        },
    )


@router.post("/my-performance/reviews/{review_id}/ack")
def ack_review(request: Request, review_id: str, db: Session = Depends(_get_db)):
    user = get_current_user(request)
    if not can_view_inbox(user.get("roles", []), user.get("permissions", [])):
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        performance_reviews.acknowledge(db, review_id, user["person_id"])
    except ValueError as exc:
        detail = str(exc)
        if detail == "Review not found":
            raise HTTPException(status_code=404, detail=detail) from exc
        if detail == "Forbidden":
            raise HTTPException(status_code=403, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
    return RedirectResponse(url=f"/agent/my-performance/reviews/{review_id}", status_code=303)
