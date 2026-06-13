"""Voice → structured field-service data extraction (task #48).

A field tech speaks a free-form note ("installed ONT serial HG8546M5, downstream
signal minus 21 dB, used 40 metres of drop cable, job done"); this turns it into
structured fields the mobile app pre-fills for the tech to confirm. Mirrors the
existing voice use cases: ai_gateway with primary/secondary fallback, strict-JSON
output parsing, and an audit log.

The model's self-reported ``confidence`` is returned but clamped to [0, 1] here;
the voice quality gate (task #50) applies the stricter, transcription-aware clamp.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.services.ai.gateway import ai_gateway
from app.services.ai.output_parsers import parse_json_object, require_keys
from app.services.ai.use_cases.voice_transcription import VoiceTranscription, transcribe_voice_audio
from app.services.audit_helpers import log_audit_event

_VALID_STATUSES = {"pending", "in_progress", "completed", "blocked"}

_FIELD_EXTRACTION_SYSTEM_PROMPT = """You extract structured field-service data from a technician's spoken job note.

Rules:
- Only extract facts explicitly stated in the transcript. Never invent values.
- Leave a field null/empty when the transcript does not mention it.
- Normalise equipment serials to uppercase with no spaces.
- Signal/readings: map each spoken reading to a label and its value as stated.
- materials_used: list items the tech says they consumed, with quantity if given.
- Return strict JSON only, no prose.

JSON shape:
{
  "work_status": "pending|in_progress|completed|blocked or null",
  "equipment_serial": "serial or null",
  "signal_readings": {"label": "value", ...},
  "materials_used": [{"name": "drop cable", "quantity": "40 m"}],
  "notes": "anything else worth recording",
  "confidence": 0.0
}
"""


@dataclass(frozen=True)
class FieldExtraction:
    work_status: str | None
    equipment_serial: str | None
    signal_readings: dict[str, str]
    materials_used: list[dict]
    notes: str
    confidence: float | None
    meta: dict = field(default_factory=dict)


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_status(value: object) -> str | None:
    text = _normalize_text(value).lower().replace(" ", "_")
    return text if text in _VALID_STATUSES else None


def _normalize_serial(value: object) -> str | None:
    text = _normalize_text(value).upper().replace(" ", "")
    return text or None


def _normalize_readings(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    readings: dict[str, str] = {}
    for label, reading in value.items():
        key = _normalize_text(label)
        val = _normalize_text(reading)
        if key and val:
            readings[key] = val
    return readings


def _normalize_materials(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    materials: list[dict] = []
    for item in value:
        if isinstance(item, dict):
            name = _normalize_text(item.get("name"))
            quantity = _normalize_text(item.get("quantity")) or None
        else:
            name = _normalize_text(item)
            quantity = None
        if name:
            materials.append({"name": name, "quantity": quantity})
    return materials


def _clamp_confidence(value: object) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, min(1.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def extract_field_data(
    db: Session,
    *,
    transcript: str,
    actor_person_id: str | None = None,
    request=None,
    context: str | None = None,
) -> FieldExtraction:
    """Extract structured field data from a transcript via the AI gateway."""
    normalized = _normalize_text(transcript)
    if not normalized:
        raise ValueError("Transcript is required")

    context_line = f"Job context: {_normalize_text(context)}\n" if context else ""
    prompt = f"{context_line}Transcript:\n{normalized}\n\nReturn JSON only."

    result, routing = ai_gateway.generate_with_fallback(
        db,
        primary="primary",
        fallback="secondary",
        system=_FIELD_EXTRACTION_SYSTEM_PROMPT,
        prompt=prompt,
        max_tokens=400,
    )
    parsed = parse_json_object(result.content)
    require_keys(parsed, ["work_status"])

    endpoint = str(routing.get("endpoint")) if isinstance(routing, dict) else ""
    extraction = FieldExtraction(
        work_status=_normalize_status(parsed.get("work_status")),
        equipment_serial=_normalize_serial(parsed.get("equipment_serial")),
        signal_readings=_normalize_readings(parsed.get("signal_readings")),
        materials_used=_normalize_materials(parsed.get("materials_used")),
        notes=_normalize_text(parsed.get("notes")),
        confidence=_clamp_confidence(parsed.get("confidence")),
        meta={"provider": result.provider, "model": result.model, "endpoint": endpoint},
    )

    log_audit_event(
        db,
        request,
        action="ai_voice_field_extraction",
        entity_type="voice_input",
        entity_id=None,
        actor_id=actor_person_id,
        metadata={
            "input_length": len(normalized),
            "work_status": extraction.work_status,
            "llm_provider": result.provider,
            "llm_model": result.model,
            "llm_endpoint": endpoint,
        },
        status_code=200,
        is_success=True,
    )
    return extraction


def extract_field_data_from_audio(
    db: Session,
    *,
    request,
    audio: bytes,
    filename: str,
    content_type: str,
    actor_person_id: str | None = None,
    context: str | None = None,
) -> tuple[VoiceTranscription, FieldExtraction]:
    """Transcribe audio, then extract structured field data from the transcript."""
    transcription = transcribe_voice_audio(
        db,
        request=request,
        audio=audio,
        filename=filename,
        content_type=content_type,
        actor_person_id=actor_person_id,
        context=context,
    )
    extraction = extract_field_data(
        db,
        transcript=transcription.text,
        actor_person_id=actor_person_id,
        request=request,
        context=context,
    )
    return transcription, extraction
