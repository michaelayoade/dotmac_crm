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
from app.middleware.widget_rate_limit import WidgetRateLimiter
from app.models.workforce import WorkOrderStatus
from app.services.field import tracking as tracking_service
from app.web.templates import Jinja2Templates

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")

router = APIRouter(prefix="/track", tags=["web-public-track"])


class _TrackRateLimiter(WidgetRateLimiter):
    """Reuse the widget limiter's Redis/in-memory sliding-window core for the
    unauthenticated /track/* surface (the global API limiter only covers /api)."""

    def check(self, key: str, limit: int, window_seconds: int) -> bool:
        allowed, _ = self._check_rate(key, limit, window_seconds)
        return allowed


_rate_limiter = _TrackRateLimiter()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limit(action: str, limit: int, window_seconds: int):
    """Per-IP rate-limit dependency factory → HTTP 429 when exceeded."""

    def _dep(request: Request) -> None:
        if not _rate_limiter.check(f"track:{action}:{_client_ip(request)}", limit, window_seconds):
            raise HTTPException(status_code=429, detail="Too many requests. Please slow down.")

    return _dep


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


@router.get("/{token}", response_class=HTMLResponse, dependencies=[Depends(_rate_limit("page", 30, 60))])
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


@router.get("/{token}/live", dependencies=[Depends(_rate_limit("live", 60, 60))])
def track_live(token: str, db: Session = Depends(_get_db)):
    token_row = tracking_service.tokens.get_by_token(db, token)
    state = tracking_service.token_state(token_row)
    if token_row is None or state != "ok":
        return JSONResponse({"available": False, "reason": state}, status_code=404 if state == "not_found" else 410)
    # geocode=False: the destination is static and the page already has it; this
    # unauthenticated endpoint is polled every ~10s and must not call the geocoder.
    return JSONResponse({"available": True, **tracking_service.public_state(db, token_row.work_order, geocode=False)})


@router.post("/{token}/confirm", response_class=HTMLResponse, dependencies=[Depends(_rate_limit("action", 10, 300))])
async def track_confirm(request: Request, token: str, db: Session = Depends(_get_db)):
    token_row = tracking_service.tokens.get_by_token(db, token)
    state = tracking_service.token_state(token_row)
    if token_row is None or state != "ok":
        return _unavailable(request, state)

    form = await request.form()
    if not _csrf_token_valid(request, form):
        return RedirectResponse(url=f"/track/{token}?error=csrf", status_code=303)

    try:
        tracking_service.confirm_appointment(db, token_row)
    except HTTPException as exc:
        if exc.status_code == 409:  # visit already closed
            return RedirectResponse(url=f"/track/{token}?error=closed", status_code=303)
        raise
    return RedirectResponse(url=f"/track/{token}?confirmed=1", status_code=303)


@router.post("/{token}/reschedule", response_class=HTMLResponse, dependencies=[Depends(_rate_limit("action", 10, 300))])
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
        if exc.status_code == 409:  # already pending, or visit already complete
            reason = "complete" if token_row.work_order.status == WorkOrderStatus.completed else "pending"
            return RedirectResponse(url=f"/track/{token}?reschedule={reason}", status_code=303)
        raise
    return RedirectResponse(url=f"/track/{token}?reschedule=1", status_code=303)
