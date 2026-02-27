"""Public survey routes — no authentication required."""

import logging
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.csrf import CSRF_COOKIE_NAME, generate_csrf_token, set_csrf_cookie, validate_csrf_token
from app.db import SessionLocal
from app.models.comms import CustomerSurveyStatus, SurveyInvitationStatus
from app.services.surveys import survey_invitations, survey_manager, survey_responses

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")

router = APIRouter(prefix="/s", tags=["web-public-surveys"])


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _survey_expired(survey) -> bool:
    """Check if survey is expired or closed."""
    if survey.status == CustomerSurveyStatus.closed:
        return True
    return bool(survey.expires_at and survey.expires_at < datetime.now(UTC))


def _invitation_expired(invitation) -> bool:
    """Check if invitation is expired."""
    return bool(invitation.expires_at and invitation.expires_at < datetime.now(UTC))


def _base_ctx(request: Request, **kwargs) -> dict:
    branding = getattr(request.state, "branding", {})
    return {"request": request, "branding": branding, **kwargs}


def _render_survey_form(
    request: Request,
    *,
    survey,
    invitation,
    status_code: int = 200,
    csrf_error: str | None = None,
):
    csrf_token = generate_csrf_token()
    ctx = _base_ctx(
        request,
        survey=survey,
        invitation=invitation,
        questions=survey.questions or [],
        csrf_token=csrf_token,
        csrf_error=csrf_error,
    )
    response = templates.TemplateResponse("public/surveys/respond.html", ctx, status_code=status_code)
    set_csrf_cookie(response, csrf_token)
    return response


def _csrf_token_valid(request: Request, form_data) -> bool:
    if not validate_csrf_token(request):
        return False
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    form_token = form_data.get("_csrf_token")
    if not cookie_token or not isinstance(form_token, str):
        return False
    return secrets.compare_digest(cookie_token, form_token)


# ── Public (slug-based, anonymous) ────────────────────────────────


@router.get("/{slug}", response_class=HTMLResponse)
def public_survey(request: Request, slug: str, db: Session = Depends(_get_db)):
    survey = survey_manager.get_by_slug(db, slug)
    if not survey or not survey.is_active:
        return templates.TemplateResponse("public/surveys/expired.html", _base_ctx(request), status_code=404)
    if _survey_expired(survey):
        return templates.TemplateResponse(
            "public/surveys/expired.html", _base_ctx(request, survey=survey), status_code=410
        )

    return _render_survey_form(request, survey=survey, invitation=None)


@router.post("/{slug}/submit", response_class=HTMLResponse)
async def public_survey_submit(request: Request, slug: str, db: Session = Depends(_get_db)):
    survey = survey_manager.get_by_slug(db, slug)
    if not survey or not survey.is_active or _survey_expired(survey):
        return templates.TemplateResponse("public/surveys/expired.html", _base_ctx(request), status_code=410)

    form = await request.form()
    if not _csrf_token_valid(request, form):
        return _render_survey_form(
            request,
            survey=survey,
            invitation=None,
            status_code=400,
            csrf_error="Invalid CSRF token. Please refresh the page and try again.",
        )

    answers = {}
    for q in survey.questions or []:
        key = q.get("key", "")
        val = form.get(key)
        if val is not None and val != "":
            answers[key] = val

    survey_responses.submit(db, str(survey.id), answers)

    return RedirectResponse(url=f"/s/{slug}/thank-you", status_code=303)


@router.get("/{slug}/thank-you", response_class=HTMLResponse)
def public_survey_thanks(request: Request, slug: str, db: Session = Depends(_get_db)):
    survey = survey_manager.get_by_slug(db, slug)
    message = survey.thank_you_message if survey else None
    ctx = _base_ctx(request, survey=survey, message=message)
    return templates.TemplateResponse("public/surveys/thank_you.html", ctx)


# ── Token-based (tracked) ────────────────────────────────────────


@router.get("/t/{token}", response_class=HTMLResponse)
def tracked_survey(request: Request, token: str, db: Session = Depends(_get_db)):
    invitation = survey_invitations.get_by_token(db, token)
    if not invitation:
        return templates.TemplateResponse("public/surveys/expired.html", _base_ctx(request), status_code=404)

    survey = invitation.survey
    if not survey or not survey.is_active:
        return templates.TemplateResponse("public/surveys/expired.html", _base_ctx(request), status_code=404)

    if invitation.status == SurveyInvitationStatus.completed:
        return templates.TemplateResponse("public/surveys/already_completed.html", _base_ctx(request, survey=survey))

    if _survey_expired(survey) or _invitation_expired(invitation):
        return templates.TemplateResponse(
            "public/surveys/expired.html", _base_ctx(request, survey=survey), status_code=410
        )

    # Mark as opened
    survey_invitations.mark_opened(db, invitation)
    db.commit()

    return _render_survey_form(request, survey=survey, invitation=invitation)


@router.post("/t/{token}/submit", response_class=HTMLResponse)
async def tracked_survey_submit(request: Request, token: str, db: Session = Depends(_get_db)):
    invitation = survey_invitations.get_by_token(db, token)
    if not invitation:
        return templates.TemplateResponse("public/surveys/expired.html", _base_ctx(request), status_code=404)

    survey = invitation.survey
    if not survey or _survey_expired(survey):
        return templates.TemplateResponse("public/surveys/expired.html", _base_ctx(request), status_code=410)

    if invitation.status == SurveyInvitationStatus.completed:
        return templates.TemplateResponse("public/surveys/already_completed.html", _base_ctx(request, survey=survey))

    form = await request.form()
    if not _csrf_token_valid(request, form):
        return _render_survey_form(
            request,
            survey=survey,
            invitation=invitation,
            status_code=400,
            csrf_error="Invalid CSRF token. Please refresh the page and try again.",
        )

    answers = {}
    for q in survey.questions or []:
        key = q.get("key", "")
        val = form.get(key)
        if val is not None and val != "":
            answers[key] = val

    survey_responses.submit(
        db,
        str(survey.id),
        answers,
        invitation_id=str(invitation.id),
        person_id=str(invitation.person_id),
    )

    return RedirectResponse(url=f"/s/t/{token}/thank-you", status_code=303)


@router.get("/t/{token}/thank-you", response_class=HTMLResponse)
def tracked_survey_thanks(request: Request, token: str, db: Session = Depends(_get_db)):
    invitation = survey_invitations.get_by_token(db, token)
    survey = invitation.survey if invitation else None
    message = survey.thank_you_message if survey else None
    ctx = _base_ctx(request, survey=survey, message=message)
    return templates.TemplateResponse("public/surveys/thank_you.html", ctx)
