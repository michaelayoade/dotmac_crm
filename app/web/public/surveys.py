"""Public survey routes — no authentication required."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

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

    ctx = _base_ctx(request, survey=survey, invitation=None, questions=survey.questions or [])
    return templates.TemplateResponse("public/surveys/respond.html", ctx)


@router.post("/{slug}/submit", response_class=HTMLResponse)
async def public_survey_submit(request: Request, slug: str, db: Session = Depends(_get_db)):
    survey = survey_manager.get_by_slug(db, slug)
    if not survey or not survey.is_active or _survey_expired(survey):
        return templates.TemplateResponse("public/surveys/expired.html", _base_ctx(request), status_code=410)

    form = await request.form()
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

    ctx = _base_ctx(request, survey=survey, invitation=invitation, questions=survey.questions or [])
    return templates.TemplateResponse("public/surveys/respond.html", ctx)


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
