"""Admin AI helper routes (HTMX partials)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services.ai.client import AIClientError
from app.services.ai.engine import intelligence_engine
from app.services.ai.use_cases.ticket_summary import summarize_ticket
from app.web.admin import get_current_user

templates = Jinja2Templates(directory="templates")
router = APIRouter(prefix="/ai", tags=["web-admin-ai"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/tickets/{ticket_id}/summary", response_class=HTMLResponse)
def ticket_ai_summary(
    request: Request,
    ticket_id: str,
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    actor_person_id = str(user.get("person_id")) if user else None
    try:
        result = summarize_ticket(
            db,
            request=request,
            ticket_id=ticket_id,
            actor_person_id=actor_person_id,
        )
        return templates.TemplateResponse(
            "admin/ai/_ticket_summary.html",
            {
                "request": request,
                "summary": result.summary,
                "next_actions": result.next_actions,
                "meta": result.meta,
            },
        )
    except (AIClientError, ValueError) as exc:
        return templates.TemplateResponse(
            "admin/ai/_error.html",
            {
                "request": request,
                "title": "AI Summary Unavailable",
                "message": str(exc),
            },
            status_code=200,
        )


@router.post("/tickets/{ticket_id}/triage", response_class=HTMLResponse)
def ticket_ai_triage(
    request: Request,
    ticket_id: str,
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    actor_person_id = str(user.get("person_id")) if user else None
    try:
        insight = intelligence_engine.invoke(
            db,
            persona_key="ticket_analyst",
            params={"ticket_id": ticket_id},
            entity_type="ticket",
            entity_id=ticket_id,
            trigger="on_demand",
            triggered_by_person_id=actor_person_id,
        )
        output = insight.structured_output or {}
        return templates.TemplateResponse(
            "admin/ai/_ticket_triage.html",
            {
                "request": request,
                "insight": insight,
                "output": output,
            },
        )
    except (AIClientError, ValueError) as exc:
        return templates.TemplateResponse(
            "admin/ai/_error.html",
            {
                "request": request,
                "title": "AI Triage Unavailable",
                "message": str(exc),
            },
            status_code=200,
        )


@router.post("/crm/conversations/{conversation_id}/draft-reply", response_class=HTMLResponse)
def conversation_ai_draft_reply(
    request: Request,
    conversation_id: str,
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    actor_person_id = str(user.get("person_id")) if user else None
    try:
        insight = intelligence_engine.invoke(
            db,
            persona_key="inbox_analyst",
            params={"conversation_id": conversation_id},
            entity_type="conversation",
            entity_id=conversation_id,
            trigger="on_demand",
            triggered_by_person_id=actor_person_id,
        )
        output = insight.structured_output or {}
        draft = str(output.get("draft") or "").strip()
        return templates.TemplateResponse(
            "admin/ai/_conversation_draft_reply.html",
            {
                "request": request,
                "conversation_id": conversation_id,
                "draft": draft,
                "meta": {"provider": insight.llm_provider, "model": insight.llm_model},
            },
        )
    except (AIClientError, ValueError) as exc:
        return templates.TemplateResponse(
            "admin/ai/_error.html",
            {
                "request": request,
                "title": "AI Draft Unavailable",
                "message": str(exc),
            },
            status_code=200,
        )


@router.get("/insights", response_class=HTMLResponse)
def intelligence_insights_placeholder(
    request: Request,
    db: Session = Depends(get_db),
):
    # Backward-compatible alias for old link.
    return RedirectResponse(url="/admin/intelligence/insights", status_code=302)
