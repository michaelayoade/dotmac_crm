"""Public surveys JSON API — programmatic/mobile survey delivery + collection.

Thin wrappers over app.services.surveys, mirroring the public survey web flow
(app/web/public/surveys.py): anonymous slug-based and tokenized invitation
surveys. No user login; mounted /api/v1 only (under the global API rate limiter).
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.comms import CustomerSurveyStatus, SurveyInvitationStatus
from app.services.surveys import survey_invitations, survey_manager, survey_responses

router = APIRouter(prefix="/surveys", tags=["surveys"])


class SurveyResponseSubmit(BaseModel):
    answers: dict


def _survey_expired(survey) -> bool:
    if survey.status == CustomerSurveyStatus.closed:
        return True
    return bool(survey.expires_at and survey.expires_at < datetime.now(UTC))


def _survey_out(survey) -> dict:
    return {
        "id": str(survey.id),
        "name": survey.name,
        "questions": survey.questions or [],
        "thank_you_message": survey.thank_you_message,
    }


# ── tokenized invitations (defined first: more specific path) ─────────────────


@router.get("/invitations/{token}")
def get_invitation(token: str, db: Session = Depends(get_db)):
    invitation = survey_invitations.get_by_token(db, token)
    if not invitation:
        raise HTTPException(status_code=404, detail="Invitation not found")
    survey = invitation.survey
    if not survey or not survey.is_active:
        raise HTTPException(status_code=404, detail="Survey not found")
    if invitation.status == SurveyInvitationStatus.completed:
        return {"survey": _survey_out(survey), "status": "completed", "already_completed": True}
    invitation_expired = bool(invitation.expires_at and invitation.expires_at < datetime.now(UTC))
    if _survey_expired(survey) or invitation_expired:
        raise HTTPException(status_code=410, detail="Survey is closed")
    survey_invitations.mark_opened(db, invitation)
    db.commit()
    return {"survey": _survey_out(survey), "status": invitation.status.value}


@router.post("/invitations/{token}/responses", status_code=201)
def submit_invitation_response(token: str, payload: SurveyResponseSubmit, db: Session = Depends(get_db)):
    invitation = survey_invitations.get_by_token(db, token)
    if not invitation:
        raise HTTPException(status_code=404, detail="Invitation not found")
    survey = invitation.survey
    if not survey or _survey_expired(survey):
        raise HTTPException(status_code=410, detail="Survey is closed")
    if invitation.status == SurveyInvitationStatus.completed:
        raise HTTPException(status_code=409, detail="Survey already completed")
    survey_responses.submit(
        db, str(survey.id), payload.answers, invitation_id=str(invitation.id), person_id=str(invitation.person_id)
    )
    return {"ok": True, "thank_you_message": survey.thank_you_message}


# ── anonymous slug-based ──────────────────────────────────────────────────────


@router.get("/{slug}")
def get_survey(slug: str, db: Session = Depends(get_db)):
    survey = survey_manager.get_by_slug(db, slug)
    if not survey or not survey.is_active:
        raise HTTPException(status_code=404, detail="Survey not found")
    if _survey_expired(survey):
        raise HTTPException(status_code=410, detail="Survey is closed")
    return _survey_out(survey)


@router.post("/{slug}/responses", status_code=201)
def submit_response(slug: str, payload: SurveyResponseSubmit, db: Session = Depends(get_db)):
    survey = survey_manager.get_by_slug(db, slug)
    if not survey or not survey.is_active or _survey_expired(survey):
        raise HTTPException(status_code=410, detail="Survey is unavailable")
    survey_responses.submit(db, str(survey.id), payload.answers)
    return {"ok": True, "thank_you_message": survey.thank_you_message}
