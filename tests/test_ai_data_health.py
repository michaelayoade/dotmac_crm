from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api import ai as ai_api
from app.models.ai_insight import AIInsight, AIInsightStatus, InsightDomain, InsightSeverity
from app.services.ai.personas._base import ContextQualityResult, OutputField, OutputSchema, PersonaSpec


def test_get_data_health_report_aggregates_persona_scores(monkeypatch, db_session):
    from app.services.ai import data_health as health_service

    spec = PersonaSpec(
        key="ticket_analyst",
        name="Ticket Analyst",
        domain=InsightDomain.tickets,
        description="Test persona",
        system_prompt="test",
        output_schema=OutputSchema(fields=(OutputField("title", "string", "x"),)),
        context_builder=lambda db, params: "context",
        min_context_quality=0.5,
        context_quality_scorer=lambda db, params: ContextQualityResult(
            score=float(params.get("score", 0)),
            missing_fields=list(params.get("missing", [])),
        ),
    )

    monkeypatch.setattr(health_service.persona_registry, "list_all", lambda: [spec])
    monkeypatch.setattr(
        health_service,
        "batch_scanners",
        {
            "tickets": lambda db, persona_key, limit=20: [
                ("ticket", "1", {"score": 0.8, "missing": ["priority"]}),
                ("ticket", "2", {"score": 0.4, "missing": ["priority", "category"]}),
            ]
        },
    )
    monkeypatch.setattr(health_service.ai_gateway, "enabled", lambda db: True)
    monkeypatch.setattr(health_service.ai_insights, "tokens_used_today", lambda db: 25)

    def _resolve_value(_db, _domain, key):
        if key == "intelligence_daily_token_budget":
            return 100
        if key == "intelligence_enabled":
            return True
        return True

    monkeypatch.setattr(health_service, "resolve_value", _resolve_value)

    result = health_service.get_data_health_report(db_session, sample_limit=10)
    assert result["gateway_enabled"] is True
    assert result["daily_token_budget"] == 100
    assert result["daily_tokens_used"] == 25
    assert result["daily_tokens_remaining"] == 75
    assert len(result["personas"]) == 1

    persona = result["personas"][0]
    assert persona["persona_key"] == "ticket_analyst"
    assert persona["sample_size"] == 2
    assert persona["avg_quality"] == 0.6
    assert persona["pct_sufficient"] == 50.0
    assert persona["readiness"] == "ready"
    assert persona["top_missing_fields"][0]["field"] == "priority"
    assert persona["top_missing_fields"][0]["count"] == 2
    assert result["domain_missing_fields"]["tickets"][0] == {"field": "priority", "count": 2}


def test_get_data_health_report_handles_no_candidates(monkeypatch, db_session):
    from app.services.ai import data_health as health_service

    spec = PersonaSpec(
        key="inbox_analyst",
        name="Inbox Analyst",
        domain=InsightDomain.inbox,
        description="Test persona",
        system_prompt="test",
        output_schema=OutputSchema(fields=(OutputField("title", "string", "x"),)),
        context_builder=lambda db, params: "context",
        min_context_quality=0.4,
        context_quality_scorer=lambda db, params: ContextQualityResult(score=1.0),
    )

    monkeypatch.setattr(health_service.persona_registry, "list_all", lambda: [spec])
    monkeypatch.setattr(health_service, "batch_scanners", {"inbox": lambda db, persona_key, limit=20: []})
    monkeypatch.setattr(health_service.ai_gateway, "enabled", lambda db: False)
    monkeypatch.setattr(health_service.ai_insights, "tokens_used_today", lambda db: 0)
    monkeypatch.setattr(health_service, "resolve_value", lambda db, domain, key: False)

    result = health_service.get_data_health_report(db_session)
    persona = result["personas"][0]
    assert result["gateway_enabled"] is False
    assert persona["sample_size"] == 0
    assert persona["avg_quality"] is None
    assert persona["readiness"] == "disabled"
    assert result["domain_missing_fields"]["inbox"] == []


def test_get_ai_data_health_wiring(monkeypatch, db_session):
    captured: dict[str, object] = {}
    expected = {"sample_limit": 33, "personas": []}

    def _fake_get_data_health_report(db, sample_limit=20):
        captured["db"] = db
        captured["sample_limit"] = sample_limit
        return expected

    monkeypatch.setattr(ai_api, "get_data_health_report", _fake_get_data_health_report)

    response = ai_api.get_ai_data_health(sample_limit=33, db=db_session)
    assert response == expected
    assert captured["db"] is db_session
    assert captured["sample_limit"] == 33


def test_get_data_health_trend_aggregates_by_day(db_session):
    from datetime import UTC, datetime, timedelta

    from app.services.ai import data_health as health_service

    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)
    older = now - timedelta(days=10)

    db_session.add_all(
        [
            AIInsight(
                persona_key="ticket_analyst",
                domain=InsightDomain.tickets,
                severity=InsightSeverity.suggestion,
                status=AIInsightStatus.completed,
                entity_type="ticket",
                entity_id="A",
                title="A",
                summary="A",
                structured_output={},
                llm_provider="test",
                llm_model="test",
                llm_tokens_in=1,
                llm_tokens_out=1,
                trigger="on_demand",
                context_quality_score=0.8,
                created_at=now,
            ),
            AIInsight(
                persona_key="ticket_analyst",
                domain=InsightDomain.tickets,
                severity=InsightSeverity.suggestion,
                status=AIInsightStatus.skipped,
                entity_type="ticket",
                entity_id="B",
                title="B",
                summary="B",
                structured_output={},
                llm_provider="test",
                llm_model="test",
                llm_tokens_in=0,
                llm_tokens_out=0,
                trigger="on_demand",
                context_quality_score=0.2,
                created_at=yesterday,
            ),
            AIInsight(
                persona_key="inbox_analyst",
                domain=InsightDomain.inbox,
                severity=InsightSeverity.info,
                status=AIInsightStatus.completed,
                entity_type="conversation",
                entity_id="C",
                title="C",
                summary="C",
                structured_output={},
                llm_provider="test",
                llm_model="test",
                llm_tokens_in=1,
                llm_tokens_out=1,
                trigger="on_demand",
                context_quality_score=0.9,
                created_at=older,
            ),
        ]
    )
    db_session.commit()

    trend = health_service.get_data_health_trend(db_session, days=3, persona_key="ticket_analyst", domain="tickets")
    assert trend["days"] == 3
    assert trend["persona_key"] == "ticket_analyst"
    assert trend["domain"] == "tickets"
    assert len(trend["points"]) == 3
    assert sum(point["insight_count"] for point in trend["points"]) == 2
    assert sum(point["completed_count"] for point in trend["points"]) == 1
    assert sum(point["skipped_count"] for point in trend["points"]) == 1


def test_get_ai_data_health_trend_wiring(monkeypatch, db_session):
    captured: dict[str, object] = {}
    expected = {"days": 7, "points": []}

    def _fake_get_data_health_trend(db, days=14, persona_key=None, domain=None):
        captured["db"] = db
        captured["days"] = days
        captured["persona_key"] = persona_key
        captured["domain"] = domain
        return expected

    monkeypatch.setattr(ai_api, "get_data_health_trend", _fake_get_data_health_trend)

    response = ai_api.get_ai_data_health_trend(days=7, persona_key="ticket_analyst", domain="tickets", db=db_session)
    assert response == expected
    assert captured["db"] is db_session
    assert captured["days"] == 7
    assert captured["persona_key"] == "ticket_analyst"
    assert captured["domain"] == "tickets"


def test_batch_scanners_cover_all_insight_domains():
    from app.services.ai.context_builders.batch_scanners import batch_scanners

    missing = [domain.value for domain in InsightDomain if domain.value not in batch_scanners]
    assert missing == []


def test_persist_and_get_latest_data_health_baseline_snapshot(db_session):
    from app.services.ai import data_health as health_service

    snapshot = {
        "captured_at": "2026-02-25T00:00:00+00:00",
        "persona_count": 8,
        "readiness_counts": {"ready": 6, "degraded": 1, "disabled": 1, "no_candidates": 0},
    }
    health_service.persist_data_health_baseline_snapshot(db_session, snapshot)
    latest = health_service.get_latest_data_health_baseline_snapshot(db_session)
    assert isinstance(latest, dict)
    assert latest["captured_at"] == snapshot["captured_at"]
    assert latest["persona_count"] == 8


def test_persist_data_health_baseline_rolls_previous_snapshot(db_session):
    from app.services.ai import data_health as health_service

    first = {"captured_at": "2026-02-24T00:00:00+00:00", "risk_inventory": []}
    second = {"captured_at": "2026-02-25T00:00:00+00:00", "risk_inventory": []}
    health_service.persist_data_health_baseline_snapshot(db_session, first)
    health_service.persist_data_health_baseline_snapshot(db_session, second)

    latest = health_service.get_latest_data_health_baseline_snapshot(db_session)
    previous = health_service.get_previous_data_health_baseline_snapshot(db_session)
    assert latest and latest["captured_at"] == second["captured_at"]
    assert previous and previous["captured_at"] == first["captured_at"]


def test_build_data_health_baseline_snapshot_wiring(monkeypatch, db_session):
    from app.services.ai import data_health as health_service

    monkeypatch.setattr(
        health_service,
        "get_data_health_report",
        lambda db, sample_limit=20: {
            "sample_limit": sample_limit,
            "gateway_enabled": True,
            "engine_scheduled_enabled": True,
            "daily_tokens_used": 10,
            "daily_tokens_remaining": 90,
            "personas": [
                {
                    "domain": "tickets",
                    "readiness": "ready",
                    "top_missing_fields": [{"field": "priority", "count": 3}],
                }
            ],
        },
    )
    monkeypatch.setattr(
        health_service,
        "get_data_health_trend",
        lambda db, days=14, persona_key=None, domain=None: {
            "days": days,
            "points": [{"date": "2026-02-25", "avg_quality": 0.7, "insight_count": 3}],
        },
    )
    monkeypatch.setattr(
        health_service,
        "_build_data_quality_risk_inventory",
        lambda db: [
            {
                "source_key": "imports_sync",
                "label": "Imports and Sync Pipelines",
                "failure_count": 7,
                "severity": "medium",
                "severity_rank": 2,
                "rationale": "sync failures",
            }
        ],
    )

    snapshot = health_service.build_data_health_baseline_snapshot(db_session, sample_limit=11, trend_days=9)
    assert snapshot["sample_limit"] == 11
    assert snapshot["trend_days"] == 9
    assert snapshot["readiness_counts"]["ready"] == 1
    assert snapshot["top_missing_fields"][0]["field"] == "priority"
    assert snapshot["risk_inventory"][0]["source_key"] == "imports_sync"


def test_get_ai_data_health_baseline_latest_wiring(monkeypatch, db_session):
    captured: dict[str, object] = {}
    expected = {"captured_at": "2026-02-25T00:00:00+00:00"}
    expected_previous = {"captured_at": "2026-02-24T00:00:00+00:00"}
    expected_deltas = {"imports_sync": {"delta_failures": 1, "trend": "up"}}
    expected_alerts = {"has_alerts": True, "count": 1, "items": [{"source_key": "imports_sync"}]}

    def _fake_get_latest_data_health_baseline_snapshot(db):
        captured["db"] = db
        return expected

    def _fake_get_previous_data_health_baseline_snapshot(db):
        captured["prev_db"] = db
        return expected_previous

    def _fake_compute_risk_inventory_deltas(latest_snapshot, previous_snapshot):
        captured["latest_snapshot"] = latest_snapshot
        captured["previous_snapshot"] = previous_snapshot
        return expected_deltas

    def _fake_compute_effective_risk_alerts(db, latest_snapshot, previous_snapshot):
        captured["alerts_db"] = db
        captured["latest_snapshot_alerts"] = latest_snapshot
        captured["previous_snapshot_alerts"] = previous_snapshot
        return expected_alerts

    monkeypatch.setattr(ai_api, "get_latest_data_health_baseline_snapshot", _fake_get_latest_data_health_baseline_snapshot)
    monkeypatch.setattr(ai_api, "get_previous_data_health_baseline_snapshot", _fake_get_previous_data_health_baseline_snapshot)
    monkeypatch.setattr(ai_api, "compute_risk_inventory_deltas", _fake_compute_risk_inventory_deltas)
    monkeypatch.setattr(ai_api, "compute_effective_risk_alerts", _fake_compute_effective_risk_alerts)

    response = ai_api.get_ai_data_health_baseline_latest(auth={"person_id": "p1"}, db=db_session)
    assert response == {
        "snapshot": expected,
        "previous_snapshot": expected_previous,
        "risk_deltas": expected_deltas,
        "risk_alerts": expected_alerts,
        "alert_state_actor": "p1",
    }
    assert captured["db"] is db_session
    assert captured["prev_db"] is db_session
    assert captured["latest_snapshot"] == expected
    assert captured["previous_snapshot"] == expected_previous
    assert captured["alerts_db"] is db_session
    assert captured["latest_snapshot_alerts"] == expected
    assert captured["previous_snapshot_alerts"] == expected_previous


def test_capture_ai_data_health_baseline_wiring(monkeypatch, db_session):
    captured: dict[str, object] = {}
    snapshot = {"captured_at": "2026-02-25T00:00:00+00:00"}

    def _fake_build_data_health_baseline_snapshot(db, sample_limit=20, trend_days=14):
        captured["db"] = db
        captured["sample_limit"] = sample_limit
        captured["trend_days"] = trend_days
        return snapshot

    def _fake_persist_data_health_baseline_snapshot(db, payload):
        captured["persist_db"] = db
        captured["payload"] = payload
        return payload

    monkeypatch.setattr(ai_api, "build_data_health_baseline_snapshot", _fake_build_data_health_baseline_snapshot)
    monkeypatch.setattr(ai_api, "persist_data_health_baseline_snapshot", _fake_persist_data_health_baseline_snapshot)

    response = ai_api.capture_ai_data_health_baseline(sample_limit=33, trend_days=21, db=db_session)
    assert response == {"snapshot": snapshot}
    assert captured["db"] is db_session
    assert captured["sample_limit"] == 33
    assert captured["trend_days"] == 21
    assert captured["persist_db"] is db_session
    assert captured["payload"] == snapshot


def test_build_data_quality_risk_inventory_has_expected_sources(db_session):
    from app.services.ai import data_health as health_service

    risks = health_service._build_data_quality_risk_inventory(db_session, days=7)
    assert len(risks) == 4
    keys = {item["source_key"] for item in risks}
    assert keys == {"inbox_ingest", "ticket_assignment", "admin_manual_edits", "imports_sync"}
    for item in risks:
        assert item["owner_role"]
        assert isinstance(item["immediate_actions"], list)
        assert len(item["immediate_actions"]) >= 1


def test_compute_risk_inventory_deltas():
    from app.services.ai import data_health as health_service

    latest = {
        "risk_inventory": [
            {"source_key": "inbox_ingest", "failure_count": 8},
            {"source_key": "imports_sync", "failure_count": 2},
        ]
    }
    previous = {
        "risk_inventory": [
            {"source_key": "inbox_ingest", "failure_count": 5},
            {"source_key": "imports_sync", "failure_count": 2},
        ]
    }
    deltas = health_service.compute_risk_inventory_deltas(latest, previous)
    assert deltas["inbox_ingest"]["delta_failures"] == 3
    assert deltas["inbox_ingest"]["trend"] == "up"
    assert deltas["imports_sync"]["delta_failures"] == 0
    assert deltas["imports_sync"]["trend"] == "flat"


def test_compute_risk_alerts():
    from app.services.ai import data_health as health_service

    latest = {
        "risk_inventory": [
            {"source_key": "inbox_ingest", "label": "Inbox Ingest", "severity": "high", "failure_count": 21},
            {"source_key": "imports_sync", "label": "Imports", "severity": "medium", "failure_count": 8},
        ]
    }
    previous = {
        "risk_inventory": [
            {"source_key": "inbox_ingest", "failure_count": 12},
            {"source_key": "imports_sync", "failure_count": 10},
        ]
    }
    alerts = health_service.compute_risk_alerts(latest, previous)
    assert alerts["has_alerts"] is True
    assert alerts["count"] == 1
    assert alerts["items"][0]["source_key"] == "inbox_ingest"
    assert alerts["items"][0]["delta_failures"] == 9


def test_compute_effective_risk_alerts_suppressed_by_snooze(monkeypatch, db_session):
    from app.services.ai import data_health as health_service

    latest = {"captured_at": "2026-02-25T12:00:00+00:00", "risk_inventory": []}
    previous = {"captured_at": "2026-02-24T12:00:00+00:00", "risk_inventory": []}

    monkeypatch.setattr(
        health_service,
        "compute_risk_alerts",
        lambda latest_snapshot, previous_snapshot: {"has_alerts": True, "count": 1, "items": [{}]},
    )
    monkeypatch.setattr(
        health_service,
        "get_risk_alert_state",
        lambda db: {
            "acknowledged_at": None,
            "acknowledged_by": None,
            "snooze_until": health_service._parse_iso_datetime("2099-01-01T00:00:00+00:00"),
            "snoozed_by": "u1",
        },
    )

    result = health_service.compute_effective_risk_alerts(
        db_session, latest_snapshot=latest, previous_snapshot=previous
    )
    assert result["has_alerts"] is False
    assert result["suppression_reason"] == "snoozed"
    assert result["raw_count"] == 1
    assert result["snoozed_by"] == "u1"


def test_acknowledge_and_snooze_api_wiring(monkeypatch, db_session):
    captured: dict[str, object] = {}

    def _fake_ack(db, actor_person_id=None):
        captured["ack_db"] = db
        captured["ack_actor"] = actor_person_id
        return {"acknowledged_at": "2026-02-25T10:00:00+00:00"}

    def _fake_snooze(db, hours=24, actor_person_id=None):
        captured["snooze_db"] = db
        captured["hours"] = hours
        captured["snooze_actor"] = actor_person_id
        return {"snooze_until": "2026-02-26T10:00:00+00:00"}

    monkeypatch.setattr(ai_api, "acknowledge_risk_alerts", _fake_ack)
    monkeypatch.setattr(ai_api, "snooze_risk_alerts", _fake_snooze)

    ack_response = ai_api.acknowledge_ai_data_health_alerts(auth={"person_id": "u-1"}, db=db_session)
    snooze_response = ai_api.snooze_ai_data_health_alerts(hours=24, auth={"person_id": "u-2"}, db=db_session)

    assert ack_response == {"ok": True, "acknowledged": {"acknowledged_at": "2026-02-25T10:00:00+00:00"}}
    assert snooze_response == {"ok": True, "snooze": {"snooze_until": "2026-02-26T10:00:00+00:00"}}
    assert captured["ack_db"] is db_session
    assert captured["ack_actor"] == "u-1"
    assert captured["snooze_db"] is db_session
    assert captured["hours"] == 24
    assert captured["snooze_actor"] == "u-2"


def test_snooze_api_rejects_invalid_hours(db_session):
    with pytest.raises(HTTPException) as exc:
        ai_api.snooze_ai_data_health_alerts(hours=12, auth={"person_id": "u-2"}, db=db_session)
    assert exc.value.status_code == 400
    assert "hours must be one of: 8, 24, 72" in str(exc.value.detail)


def test_snooze_service_rejects_invalid_hours(db_session):
    from app.services.ai import data_health as health_service

    with pytest.raises(ValueError) as exc:
        health_service.snooze_risk_alerts(db_session, hours=12, actor_person_id="u-2")
    assert "hours must be one of: 8, 24, 72" in str(exc.value)
