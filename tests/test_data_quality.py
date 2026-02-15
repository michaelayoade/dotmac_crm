"""Tests for the data quality scoring module."""

from __future__ import annotations

import uuid

from app.services.data_quality.scoring import (
    EntityQualityResult,
    _missing,
    _weighted_score,
    score_project_quality,
    score_ticket_quality,
    score_work_order_quality,
)

# ---------------------------------------------------------------------------
# EntityQualityResult unit tests
# ---------------------------------------------------------------------------


def test_entity_quality_result_sufficient():
    r = EntityQualityResult("test", "123", 0.5, {}, [])
    assert r.sufficient is True


def test_entity_quality_result_insufficient():
    r = EntityQualityResult("test", "123", 0.2, {}, [])
    assert r.sufficient is False


def test_entity_quality_result_pct():
    r = EntityQualityResult("test", "123", 0.75, {}, [])
    assert r.pct() == 75


def test_entity_quality_result_pct_zero():
    r = EntityQualityResult("test", "123", 0.0, {}, [])
    assert r.pct() == 0


# ---------------------------------------------------------------------------
# Weighted score helpers
# ---------------------------------------------------------------------------


def test_weighted_score_basic():
    scores = {"a": 1.0, "b": 0.5}
    weights = {"a": 0.6, "b": 0.4}
    result = _weighted_score(scores, weights)
    assert result == 0.8  # 1.0*0.6 + 0.5*0.4


def test_weighted_score_clamps():
    scores = {"a": 2.0}
    weights = {"a": 1.0}
    assert _weighted_score(scores, weights) == 1.0


def test_missing_fields():
    scores = {"a": 1.0, "b": 0.0, "c": 0.5, "d": 0.0}
    result = _missing(scores)
    assert set(result) == {"b", "d"}


# ---------------------------------------------------------------------------
# Ticket scorer (uses db_session + ticket fixture)
# ---------------------------------------------------------------------------


def test_score_ticket_quality_with_fixture(db_session, ticket):
    result = score_ticket_quality(db_session, str(ticket.id))
    assert result.entity_type == "ticket"
    assert result.entity_id == str(ticket.id)
    assert 0.0 <= result.score <= 1.0
    assert isinstance(result.field_scores, dict)
    assert len(result.field_scores) > 0
    # The fixture ticket has minimal data so should have some missing fields
    assert isinstance(result.missing_fields, list)


def test_score_ticket_quality_not_found(db_session):
    fake_id = str(uuid.uuid4())
    result = score_ticket_quality(db_session, fake_id)
    assert result.score == 0.0
    assert "ticket_not_found" in result.missing_fields


# ---------------------------------------------------------------------------
# Project scorer
# ---------------------------------------------------------------------------


def test_score_project_quality_with_fixture(db_session, project):
    result = score_project_quality(db_session, str(project.id))
    assert result.entity_type == "project"
    assert result.entity_id == str(project.id)
    assert 0.0 <= result.score <= 1.0
    assert "name" in result.field_scores


def test_score_project_quality_not_found(db_session):
    result = score_project_quality(db_session, str(uuid.uuid4()))
    assert result.score == 0.0
    assert "project_not_found" in result.missing_fields


# ---------------------------------------------------------------------------
# Work order scorer
# ---------------------------------------------------------------------------


def test_score_work_order_quality_with_fixture(db_session, work_order):
    result = score_work_order_quality(db_session, str(work_order.id))
    assert result.entity_type == "work_order"
    assert result.entity_id == str(work_order.id)
    assert 0.0 <= result.score <= 1.0
    assert "title" in result.field_scores


def test_score_work_order_quality_not_found(db_session):
    result = score_work_order_quality(db_session, str(uuid.uuid4()))
    assert result.score == 0.0
    assert "work_order_not_found" in result.missing_fields


# ---------------------------------------------------------------------------
# ContextQualityResult (AI persona bridge type)
# ---------------------------------------------------------------------------


def test_context_quality_result():
    from app.services.ai.personas._base import ContextQualityResult

    r = ContextQualityResult(score=0.6, field_scores={"a": 1.0}, missing_fields=["b"])
    assert r.sufficient is True
    assert r.score == 0.6
    assert r.missing_fields == ["b"]


def test_context_quality_result_insufficient():
    from app.services.ai.personas._base import ContextQualityResult

    r = ContextQualityResult(score=0.1)
    assert r.sufficient is False


# ---------------------------------------------------------------------------
# PersonaSpec quality fields
# ---------------------------------------------------------------------------


def test_persona_spec_default_quality_scorer():
    """Default quality scorer returns 1.0 (always passes)."""
    from app.services.ai.personas._base import PersonaSpec

    spec = PersonaSpec(
        key="test",
        name="Test",
        domain="test",
        description="Test persona",
        system_prompt="test",
        output_schema=None,
        context_builder=lambda db, params: "",
    )
    result = spec.context_quality_scorer(None, {})
    assert result.score == 1.0
    assert result.sufficient is True


def test_persona_registry_has_quality_scorers():
    """All registered personas should have quality scorer fields."""
    # Import all personas to trigger registration
    import app.services.ai.personas.campaign_optimizer
    import app.services.ai.personas.customer_success
    import app.services.ai.personas.dispatch_planner
    import app.services.ai.personas.inbox_analyst
    import app.services.ai.personas.performance_coach
    import app.services.ai.personas.project_advisor
    import app.services.ai.personas.ticket_analyst
    import app.services.ai.personas.vendor_analyst  # noqa: F401
    from app.services.ai.personas._registry import persona_registry

    for key, spec in persona_registry._personas.items():
        assert hasattr(spec, "context_quality_scorer"), f"{key} missing context_quality_scorer"
        assert hasattr(spec, "min_context_quality"), f"{key} missing min_context_quality"
        assert hasattr(spec, "skip_on_low_quality"), f"{key} missing skip_on_low_quality"
        assert isinstance(spec.min_context_quality, float), f"{key} min_context_quality not float"


# ---------------------------------------------------------------------------
# Reports module
# ---------------------------------------------------------------------------


def test_domain_health_report_tickets(db_session, ticket):
    from app.services.data_quality.reports import domain_health_report

    report = domain_health_report(db_session, "tickets")
    assert report.domain == "tickets"
    assert report.entity_count >= 1
    assert 0.0 <= report.avg_quality <= 1.0
    assert 0 <= report.avg_pct() <= 100


def test_domain_health_report_unknown_domain(db_session):
    import pytest

    from app.services.data_quality.reports import domain_health_report

    with pytest.raises(ValueError, match="Unknown domain"):
        domain_health_report(db_session, "nonexistent")


def test_all_domains_health(db_session):
    from app.services.data_quality.reports import all_domains_health

    reports = all_domains_health(db_session)
    assert isinstance(reports, list)
    domains = [r.domain for r in reports]
    assert "tickets" in domains
    assert "projects" in domains


def test_domain_entity_list(db_session, ticket):
    from app.services.data_quality.reports import domain_entity_list

    results, total = domain_entity_list(db_session, "tickets", limit=10, offset=0)
    assert total >= 1
    assert len(results) >= 1
    assert results[0].entity_type == "ticket"
