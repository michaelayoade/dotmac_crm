"""POST /field/voice/extract — extraction + quality gate wiring (tasks #48/#49/#50)."""

import json
from types import SimpleNamespace

from app.api.field.voice import extract_voice
from app.schemas.field import VoiceExtractRequest
from app.services.ai import gateway as gateway_module


def _mock_gateway(monkeypatch, payload: dict):
    response = SimpleNamespace(content=json.dumps(payload), provider="vllm", model="m", tokens_in=1, tokens_out=1)
    monkeypatch.setattr(
        gateway_module.ai_gateway,
        "generate_with_fallback",
        lambda db, **kw: (response, {"endpoint": "primary"}),
    )


def test_extract_returns_fields_and_no_review_for_clean_input(db_session, person, monkeypatch):
    _mock_gateway(
        monkeypatch,
        {"work_status": "completed", "equipment_serial": "hg8546", "confidence": 0.9},
    )
    resp = extract_voice(
        payload=VoiceExtractRequest(transcript="installed the ont serial hg eight five four six all good"),
        request=None,
        auth={"person_id": str(person.id)},
        db=db_session,
    )
    assert resp.work_status == "completed"
    assert resp.equipment_serial == "HG8546"
    assert resp.confidence == 0.9
    assert resp.requires_review is False


def test_short_transcript_forces_review(db_session, person, monkeypatch):
    _mock_gateway(monkeypatch, {"work_status": "completed", "confidence": 0.95})
    resp = extract_voice(
        payload=VoiceExtractRequest(transcript="done"),
        request=None,
        auth={"person_id": str(person.id)},
        db=db_session,
    )
    # Quality gate clamps the optimistic model confidence on a 1-word transcript.
    assert resp.requires_review is True
    assert "transcript_too_short" in resp.review_reasons
    assert resp.confidence <= 0.3


def test_low_asr_confidence_forces_review(db_session, person, monkeypatch):
    _mock_gateway(monkeypatch, {"work_status": "in_progress", "confidence": 0.9})
    resp = extract_voice(
        payload=VoiceExtractRequest(
            transcript="installed the ont and tested the downstream line carefully",
            asr_confidence=0.35,
        ),
        request=None,
        auth={"person_id": str(person.id)},
        db=db_session,
    )
    assert resp.requires_review is True
    assert "low_asr_confidence" in resp.review_reasons
