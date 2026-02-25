from __future__ import annotations

from app.api import ai as ai_api
from app.models.ai_insight import AIInsight, AIInsightStatus, InsightDomain, InsightSeverity
from app.services.ai.insights import ai_insights


def _create_insight(db_session) -> AIInsight:
    insight = AIInsight(
        persona_key="ticket_analyst",
        domain=InsightDomain.tickets,
        severity=InsightSeverity.suggestion,
        status=AIInsightStatus.completed,
        entity_type="ticket",
        entity_id="TCK-1",
        title="Test insight",
        summary="Test summary",
        structured_output={"title": "Test"},
        confidence_score=None,
        recommendations=["Do thing"],
        context_quality_score=0.5,
        llm_provider="test",
        llm_model="test-model",
        llm_tokens_in=10,
        llm_tokens_out=20,
        llm_endpoint="primary",
        generation_time_ms=100,
        trigger="on_demand",
    )
    db_session.add(insight)
    db_session.commit()
    db_session.refresh(insight)
    return insight


def test_ai_insight_action_updates_status(db_session):
    insight = _create_insight(db_session)
    updated = ai_insights.action(db_session, str(insight.id))
    assert updated.status == AIInsightStatus.actioned


def test_ai_insight_expire_updates_status(db_session):
    insight = _create_insight(db_session)
    updated = ai_insights.expire(db_session, str(insight.id))
    assert updated.status == AIInsightStatus.expired


def test_api_action_insight_wiring(monkeypatch, db_session):
    captured: dict[str, object] = {}
    expected = {"id": "1", "status": "actioned"}

    class _Obj:
        id = "1"
        status = type("S", (), {"value": "actioned"})()

    def _fake_action(db, insight_id, person_id=None):
        captured["db"] = db
        captured["insight_id"] = insight_id
        captured["person_id"] = person_id
        return _Obj()

    monkeypatch.setattr(ai_api.ai_insights, "action", _fake_action)
    auth = {"person_id": "00000000-0000-0000-0000-000000000001"}
    response = ai_api.action_insight("abc", db=db_session, auth=auth)
    assert response == expected
    assert captured["db"] is db_session
    assert captured["insight_id"] == "abc"
    assert captured["person_id"] == auth["person_id"]


def test_api_expire_insight_wiring(monkeypatch, db_session):
    captured: dict[str, object] = {}
    expected = {"id": "2", "status": "expired"}

    class _Obj:
        id = "2"
        status = type("S", (), {"value": "expired"})()

    def _fake_expire(db, insight_id):
        captured["db"] = db
        captured["insight_id"] = insight_id
        return _Obj()

    monkeypatch.setattr(ai_api.ai_insights, "expire", _fake_expire)
    response = ai_api.expire_insight("xyz", db=db_session)
    assert response == expected
    assert captured["db"] is db_session
    assert captured["insight_id"] == "xyz"
