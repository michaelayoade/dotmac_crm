from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.field import VoiceExtractRequest, VoiceExtractResponse
from app.services.ai.use_cases.voice_field_extraction import extract_field_data
from app.services.ai.voice_quality import clamp_confidence
from app.services.auth_dependencies import require_user_auth

router = APIRouter(prefix="/voice", tags=["field-voice"])


@router.post("/extract", response_model=VoiceExtractResponse)
def extract_voice(
    payload: VoiceExtractRequest,
    request: Request,
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    """Extract structured field data from a transcript and apply the quality gate.

    The mobile app sends the on-device transcript (and optional ASR confidence);
    the response carries the structured fields plus requires_review so the app
    knows whether to force a manual confirmation (tasks #48 + #50).
    """
    extraction = extract_field_data(
        db,
        transcript=payload.transcript,
        actor_person_id=auth["person_id"],
        request=request,
        context=payload.context,
    )
    verdict = clamp_confidence(
        extraction.confidence,
        transcript=payload.transcript,
        asr_confidence=payload.asr_confidence,
    )
    return VoiceExtractResponse(
        work_status=extraction.work_status,
        equipment_serial=extraction.equipment_serial,
        signal_readings=extraction.signal_readings,
        materials_used=extraction.materials_used,
        notes=extraction.notes,
        confidence=verdict.confidence,
        requires_review=verdict.requires_review,
        review_reasons=verdict.reasons,
    )
