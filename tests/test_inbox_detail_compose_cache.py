from types import SimpleNamespace

from app.services.crm.inbox import cache as inbox_cache
from app.services.crm.inbox import page_context


def test_load_message_template_choices_uses_process_cache(monkeypatch):
    calls = {"count": 0}

    def fake_list(_db, **_kwargs):
        calls["count"] += 1
        return [
            SimpleNamespace(
                id="tmpl-1",
                name="Welcome",
                body="Hello",
                channel_type=SimpleNamespace(value="whatsapp"),
            )
        ]

    monkeypatch.setattr(page_context.message_templates, "list", fake_list)
    inbox_cache.invalidate_prefix("inbox_detail:message_templates:")
    try:
        first = page_context._load_message_template_choices(SimpleNamespace())
        second = page_context._load_message_template_choices(SimpleNamespace())
    finally:
        inbox_cache.invalidate_prefix("inbox_detail:message_templates:")

    assert first == second
    assert first[0]["channel_type"] == "whatsapp"
    assert calls["count"] == 1


def test_load_macro_choices_uses_process_cache(monkeypatch):
    calls = {"count": 0}

    def fake_list_for_agent(_db, agent_id):
        calls["count"] += 1
        return [
            SimpleNamespace(
                id="macro-1",
                name="Escalate",
                description="Escalate to support",
                actions=[{"action_type": "add_tag"}],
                visibility=SimpleNamespace(value="shared"),
            )
        ]

    monkeypatch.setattr(page_context.conversation_macros, "list_for_agent", fake_list_for_agent)
    inbox_cache.invalidate_prefix("inbox_detail:macros:")
    try:
        first = page_context._load_macro_choices(SimpleNamespace(), "agent-1")
        second = page_context._load_macro_choices(SimpleNamespace(), "agent-1")
    finally:
        inbox_cache.invalidate_prefix("inbox_detail:macros:")

    assert first == second
    assert first[0]["action_count"] == 1
    assert first[0]["visibility"] == "shared"
    assert calls["count"] == 1
