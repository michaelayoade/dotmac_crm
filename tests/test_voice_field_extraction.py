"""Voice → structured field-data extraction (task #48)."""

import json
from types import SimpleNamespace

import pytest

from app.services.ai import gateway as gateway_module
from app.services.ai.client import AIClientError
from app.services.ai.use_cases import voice_field_extraction as vfe


def _mock_gateway(monkeypatch, payload: dict):
    response = SimpleNamespace(content=json.dumps(payload), provider="vllm", model="test-model", tokens_in=10, tokens_out=20)

    def _fake(db, **kwargs):
        return response, {"endpoint": "primary", "fallback_used": False}

    monkeypatch.setattr(gateway_module.ai_gateway, "generate_with_fallback", _fake)


def test_extraction_normalizes_fields(db_session, monkeypatch):
    _mock_gateway(
        monkeypatch,
        {
            "work_status": "completed",
            "equipment_serial": "hg8546 m5",
            "signal_readings": {"Downstream": "-21 dB", "": "ignored", "Upstream": ""},
            "materials_used": [{"name": "drop cable", "quantity": "40 m"}, "cable ties", {"name": ""}],
            "notes": "  customer   happy ",
            "confidence": 0.82,
        },
    )

    result = vfe.extract_field_data(db_session, transcript="installed the ONT, all good")

    assert result.work_status == "completed"
    assert result.equipment_serial == "HG8546M5"  # uppercased, despaced
    assert result.signal_readings == {"Downstream": "-21 dB"}  # blank label/value dropped
    assert result.materials_used == [
        {"name": "drop cable", "quantity": "40 m"},
        {"name": "cable ties", "quantity": None},
    ]
    assert result.notes == "customer happy"
    assert result.confidence == 0.82
    assert result.meta["model"] == "test-model"


def test_invalid_status_becomes_none(db_session, monkeypatch):
    _mock_gateway(monkeypatch, {"work_status": "halfway", "confidence": 0.5})
    result = vfe.extract_field_data(db_session, transcript="some note")
    assert result.work_status is None


def test_confidence_clamped(db_session, monkeypatch):
    _mock_gateway(monkeypatch, {"work_status": "completed", "confidence": 1.7})
    result = vfe.extract_field_data(db_session, transcript="done")
    assert result.confidence == 1.0

    _mock_gateway(monkeypatch, {"work_status": "completed", "confidence": "not-a-number"})
    result = vfe.extract_field_data(db_session, transcript="done")
    assert result.confidence is None


def test_empty_transcript_rejected(db_session, monkeypatch):
    _mock_gateway(monkeypatch, {"work_status": "completed"})
    with pytest.raises(ValueError):
        vfe.extract_field_data(db_session, transcript="   ")


def test_missing_required_key_raises(db_session, monkeypatch):
    # Model omits work_status entirely → require_keys rejects it.
    _mock_gateway(monkeypatch, {"notes": "no status here"})
    with pytest.raises(AIClientError):
        vfe.extract_field_data(db_session, transcript="ambiguous note")
