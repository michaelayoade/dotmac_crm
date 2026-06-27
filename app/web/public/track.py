"""Public "Track My Visit" routes — no authentication; the magic-link token authorizes.

Mirrors the public-survey pattern (token lookup, standalone templates, double-submit
CSRF on POSTs). The page reads work-order field events as the customer-facing source
of truth and offers two routed actions: confirm and request-reschedule.
"""

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.csrf import CSRF_COOKIE_NAME, generate_csrf_token, set_csrf_cookie, validate_csrf_token
from app.db import SessionLocal
from app.services.field import tracking as tracking_service
from app.web.templates import Jinja2Templates

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")

router = APIRouter(prefix="/track", tags=["web-public-track"])

# Token-state → friendly page + HTTP status for the unavailable cases.
_UNAVAILABLE = {
    "not_found": (404, "This tracking link is not valid."),
    "expired": (410, "This tracking link has expired."),
    "closed": (410, "This visit is complete. The tracking link is now closed."),
}


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _base_ctx(request: Request, **kwargs) -> dict:
    branding = getattr(request.state, "branding", {})
    return {"request": request, "branding": branding, **kwargs}


def _csrf_token_valid(request: Request, form_data) -> bool:
    if not validate_csrf_token(request):
        return False
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    form_token = form_data.get("_csrf_token")
    if not cookie_token or not isinstance(form_token, str):
        return False
    return secrets.compare_digest(cookie_token, form_token)


def _unavailable(request: Request, state: str) -> HTMLResponse:
    status_code, message = _UNAVAILABLE.get(state, _UNAVAILABLE["not_found"])
    return templates.TemplateResponse(
        "public/track/expired.html",
        _base_ctx(request, message=message),
        status_code=status_code,
    )


@router.get("/{token}", response_class=HTMLResponse)
def track_visit(request: Request, token: str, db: Session = Depends(_get_db)):
    token_row = tracking_service.tokens.get_by_token(db, token)
    state = tracking_service.token_state(token_row)
    if token_row is None or state != "ok":
        return _unavailable(request, state)

    tracking_service.tokens.mark_accessed(db, token_row)
    visit_state = tracking_service.public_state(db, token_row.work_order)
    csrf_token = generate_csrf_token()
    response = templates.TemplateResponse(
        "public/track/visit.html",
        _base_ctx(request, token=token, state=visit_state, csrf_token=csrf_token),
    )
    set_csrf_cookie(response, csrf_token)
    return response


@router.get("/{token}/live")
def track_live(token: str, db: Session = Depends(_get_db)):
    token_row = tracking_service.tokens.get_by_token(db, token)
    state = tracking_service.token_state(token_row)
    if token_row is None or state != "ok":
        return JSONResponse({"available": False, "reason": state}, status_code=404 if state == "not_found" else 410)
    return JSONResponse({"available": True, **tracking_service.public_state(db, token_row.work_order)})


@router.post("/{token}/confirm", response_class=HTMLResponse)
async def track_confirm(request: Request, token: str, db: Session = Depends(_get_db)):
    token_row = tracking_service.tokens.get_by_token(db, token)
    state = tracking_service.token_state(token_row)
    if token_row is None or state != "ok":
        return _unavailable(request, state)

    form = await request.form()
    if not _csrf_token_valid(request, form):
        return RedirectResponse(url=f"/track/{token}?error=csrf", status_code=303)

    tracking_service.confirm_appointment(db, token_row)
    return RedirectResponse(url=f"/track/{token}?confirmed=1", status_code=303)


@router.post("/{token}/reschedule", response_class=HTMLResponse)
async def track_reschedule(request: Request, token: str, db: Session = Depends(_get_db)):
    token_row = tracking_service.tokens.get_by_token(db, token)
    state = tracking_service.token_state(token_row)
    if token_row is None or state != "ok":
        return _unavailable(request, state)

    form = await request.form()
    if not _csrf_token_valid(request, form):
        return RedirectResponse(url=f"/track/{token}?error=csrf", status_code=303)

    note = form.get("note")
    preferred_window = form.get("preferred_window")
    try:
        tracking_service.request_reschedule(
            db,
            token_row,
            note=note if isinstance(note, str) else None,
            preferred_window=preferred_window if isinstance(preferred_window, str) else None,
        )
    except HTTPException as exc:
        if exc.status_code == 409:  # one is already pending
            return RedirectResponse(url=f"/track/{token}?reschedule=pending", status_code=303)
        raise
    return RedirectResponse(url=f"/track/{token}?reschedule=1", status_code=303)
