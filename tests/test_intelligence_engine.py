from __future__ import annotations

import json

import pytest

from app.models.domain_settings import SettingValueType
from app.models.tickets import Ticket, TicketChannel, TicketPriority, TicketStatus
from app.services.ai.client import AIClientError, AIResponse
from app.services.ai.engine import intelligence_engine
from app.services.ai.gateway import ai_gateway
from app.services.domain_settings import integration_settings


def _seed_ai_enabled(db_session) -> None:
    integration_settings.ensure_by_key(
        db_session,
        key="ai_enabled",
        value_type=SettingValueType.boolean,
        value_text="true",
        value_json=True,
    )
    # Allow on-demand engine runs even if intelligence_enabled is false.
    integration_settings.ensure_by_key(
        db_session,
        key="intelligence_enabled",
        value_type=SettingValueType.boolean,
        value_text="false",
        value_json=False,
    )
    integration_settings.ensure_by_key(
        db_session,
        key="intelligence_daily_token_budget",
        value_type=SettingValueType.integer,
        value_text="0",
    )


def test_engine_invoke_persists_insight(monkeypatch, db_session):
    _seed_ai_enabled(db_session)

    # Minimal ticket for ticket_analyst context builder.
    t = Ticket(
        title="Internet down at customer site",
        description="Customer reports complete outage since morning.",
        status=TicketStatus.open,
        priority=TicketPriority.urgent,
        channel=TicketChannel.web,
        is_active=True,
    )
    db_session.add(t)
    db_session.commit()

    payload = {
        "priority_score": 95,
        "category": "technical",
        "sentiment": "frustrated",
        "escalation_risk": "high",
        "title": "Outage triage needed",
        "summary": "Customer reports complete outage. Likely service disruption requiring immediate checks.",
        "recommended_actions": ["Check upstream link status", "Confirm customer CPE power/cabling", "Escalate to NOC"],
        "confidence": 0.7,
    }

    def _fake_generate_with_fallback(*args, **kwargs):
        return (
            AIResponse(
                content=json.dumps(payload),
                tokens_in=50,
                tokens_out=120,
                model="test-model",
                provider="test-provider",
            ),
            {"endpoint": "primary", "fallback_used": False},
        )

    monkeypatch.setattr(ai_gateway, "generate_with_fallback", _fake_generate_with_fallback)

    insight = intelligence_engine.invoke(
        db_session,
        persona_key="ticket_analyst",
        params={"ticket_id": str(t.id)},
        entity_type="ticket",
        entity_id=str(t.id),
        trigger="on_demand",
        triggered_by_person_id=None,
    )

    assert insight.id is not None
    assert insight.persona_key == "ticket_analyst"
    assert insight.domain.value == "tickets"
    assert insight.structured_output is not None
    assert insight.structured_output["priority_score"] == 95
    assert insight.llm_model == "test-model"
    assert insight.llm_provider == "test-provider"
    assert insight.llm_endpoint == "primary"
    assert insight.severity.value in {"critical", "warning", "suggestion", "info"}


def test_engine_scheduled_requires_intelligence_enabled(monkeypatch, db_session):
    _seed_ai_enabled(db_session)

    # Stub the gateway call; it shouldn't be reached when disabled for scheduled.
    monkeypatch.setattr(ai_gateway, "generate_with_fallback", lambda *a, **k: pytest.fail("should not call llm"))

    with pytest.raises(AIClientError):
        intelligence_engine.invoke(
            db_session,
            persona_key="ticket_analyst",
            params={"ticket_id": "00000000-0000-0000-0000-000000000000"},
            entity_type="ticket",
            entity_id=None,
            trigger="scheduled",
            triggered_by_person_id=None,
        )
