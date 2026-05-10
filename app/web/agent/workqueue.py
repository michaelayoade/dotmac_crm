"""Workqueue page + HTMX partials behind feature flag."""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.domain_settings import SettingDomain
from app.schemas.workqueue import ItemRef, SnoozeRequest
from app.services import settings_spec
from app.services.workqueue.actions import workqueue_actions
from app.services.workqueue.aggregator import build_workqueue
from app.services.workqueue.permissions import has_workqueue_view
from app.services.workqueue.types import ItemKind
from app.web.admin._auth_helpers import get_current_user, get_sidebar_stats
from app.web.auth.dependencies import require_web_auth
from app.web.templates import Jinja2Templates

router = APIRouter(
    prefix="/agent/workqueue",
    tags=["web-agent-workqueue"],
    dependencies=[Depends(require_web_auth)],
)
templates = Jinja2Templates(directory="templates")


@dataclass
class _WorkqueueUser:
    """Adapter exposing the attribute shape expected by workqueue services."""

    person_id: UUID
    permissions: set[str]


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _flag_or_404(db: Session) -> None:
    enabled = settings_spec.resolve_value(db, SettingDomain.workflow, "workqueue.enabled")
    if not enabled:
        raise HTTPException(status_code=404)


def _build_user(request: Request) -> tuple[dict, _WorkqueueUser]:
    raw = get_current_user(request)
    person_id = raw.get("person_id") or ""
    try:
        person_uuid = UUID(person_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=403) from exc
    perms = set(raw.get("permissions") or [])
    return raw, _WorkqueueUser(person_id=person_uuid, permissions=perms)


@router.get("", response_class=HTMLResponse)
def page(
    request: Request,
    db: Session = Depends(_get_db),
    audience: str | None = Query(default=None, alias="as"),
):
    _flag_or_404(db)
    raw_user, wq_user = _build_user(request)
    if not has_workqueue_view(wq_user):
        raise HTTPException(status_code=403)
    view = build_workqueue(db, wq_user, requested_audience=audience)
    return templates.TemplateResponse(
        "agent/workqueue/index.html",
        {
            "request": request,
            "current_user": raw_user,
            "sidebar_stats": get_sidebar_stats(db, raw_user),
            "active_page": "workqueue",
            "view": view,
            "right_now": view.right_now,
            "csrf_token": request.cookies.get("csrf_token", ""),
        },
    )


@router.get("/_right_now", response_class=HTMLResponse)
def partial_right_now(
    request: Request,
    db: Session = Depends(_get_db),
    audience: str | None = Query(default=None, alias="as"),
):
    _flag_or_404(db)
    _, wq_user = _build_user(request)
    if not has_workqueue_view(wq_user):
        raise HTTPException(status_code=403)
    view = build_workqueue(db, wq_user, requested_audience=audience)
    return templates.TemplateResponse(
        "agent/workqueue/_right_now.html",
        {
            "request": request,
            "right_now": view.right_now,
            "csrf_token": request.cookies.get("csrf_token", ""),
        },
    )


@router.get("/_section/{kind}", response_class=HTMLResponse)
def partial_section(
    kind: str,
    request: Request,
    db: Session = Depends(_get_db),
    audience: str | None = Query(default=None, alias="as"),
):
    _flag_or_404(db)
    try:
        item_kind = ItemKind(kind)
    except ValueError as exc:
        raise HTTPException(status_code=404) from exc
    _, wq_user = _build_user(request)
    if not has_workqueue_view(wq_user):
        raise HTTPException(status_code=403)
    view = build_workqueue(db, wq_user, requested_audience=audience)
    section = next((s for s in view.sections if s.kind is item_kind), None)
    if section is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        "agent/workqueue/_section.html",
        {
            "request": request,
            "section": section,
            "csrf_token": request.cookies.get("csrf_token", ""),
        },
    )


def _refresh_response(message: str) -> Response:
    """Empty 204 with HX-Trigger that refreshes the Workqueue and toasts."""
    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers={
            "HX-Trigger": json.dumps(
                {
                    "workqueue:refresh": True,
                    "showToast": {"message": message, "type": "success"},
                }
            )
        },
    )


@router.post("/snooze")
def post_snooze(
    payload: SnoozeRequest,
    request: Request,
    db: Session = Depends(_get_db),
):
    _flag_or_404(db)
    _, wq_user = _build_user(request)
    if not has_workqueue_view(wq_user):
        raise HTTPException(status_code=403)
    try:
        if payload.preset:
            workqueue_actions.snooze_preset(
                db, wq_user, payload.kind, payload.item_id, payload.preset
            )
        else:
            workqueue_actions.snooze(
                db,
                wq_user,
                payload.kind,
                payload.item_id,
                until=payload.until,
                until_next_reply=payload.until_next_reply,
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _refresh_response("Snoozed")


@router.post("/snooze/clear")
def post_clear_snooze(
    payload: ItemRef,
    request: Request,
    db: Session = Depends(_get_db),
):
    _flag_or_404(db)
    _, wq_user = _build_user(request)
    if not has_workqueue_view(wq_user):
        raise HTTPException(status_code=403)
    workqueue_actions.clear_snooze(db, wq_user, payload.kind, payload.item_id)
    return _refresh_response("Snooze cleared")


@router.post("/claim")
def post_claim(
    payload: ItemRef,
    request: Request,
    db: Session = Depends(_get_db),
):
    _flag_or_404(db)
    _, wq_user = _build_user(request)
    if not has_workqueue_view(wq_user):
        raise HTTPException(status_code=403)
    try:
        workqueue_actions.claim(db, wq_user, payload.kind, payload.item_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _refresh_response("Claimed")


@router.post("/complete")
def post_complete(
    payload: ItemRef,
    request: Request,
    db: Session = Depends(_get_db),
):
    _flag_or_404(db)
    _, wq_user = _build_user(request)
    if not has_workqueue_view(wq_user):
        raise HTTPException(status_code=403)
    try:
        workqueue_actions.complete(db, wq_user, payload.kind, payload.item_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _refresh_response("Completed")
