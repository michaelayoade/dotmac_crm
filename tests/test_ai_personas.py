from __future__ import annotations

from app.services.ai.personas import persona_registry


def test_persona_registry_contains_expected_personas():
    keys = set(persona_registry.keys())
    expected = {
        "ticket_analyst",
        "inbox_analyst",
        "project_advisor",
        "campaign_optimizer",
        "dispatch_planner",
        "vendor_analyst",
        "performance_coach",
        "customer_success",
    }
    assert expected.issubset(keys)


def test_all_personas_require_title_and_summary():
    for spec in persona_registry.list_all():
        required = set(spec.output_schema.required_keys())
        assert "title" in required
        assert "summary" in required
