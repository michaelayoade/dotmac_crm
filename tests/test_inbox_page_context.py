import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace

from app.models.crm.enums import ConversationStatus
from app.services.crm.inbox.listing import InboxListResult
from app.services.crm.inbox.page_context import build_inbox_conversation_detail_context, build_inbox_page_context


def _run_async(coro):
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()


def test_build_inbox_conversation_detail_context_exposes_safe_attribution_subset(monkeypatch):
    conversation = SimpleNamespace(
        id="conv-1",
        status=ConversationStatus.open,
        metadata_={
            "attribution": {
                "source": "ADS",
                "ad_id": "ad-1",
                "campaign_id": "camp-1",
                "ctwa_clid": "clid-1",
                "source_url": "https://m.me/example",
                "referral": {"headline": "Fiber promo"},
                "raw_blob": {"do_not": "expose"},
            }
        },
    )
    thread = SimpleNamespace(
        kind="success",
        conversation=conversation,
        messages=[],
    )

    monkeypatch.setattr(
        "app.services.crm.inbox.thread.load_conversation_thread",
        lambda *_args, **_kwargs: thread,
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context.format_conversation_for_template",
        lambda *_args, **_kwargs: {"id": "conv-1"},
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context.enrich_formatted_conversations_with_labels",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context._load_assignment_activity",
        lambda *_args, **_kwargs: ([], None),
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context.get_conversation_csat_event",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context.get_current_agent_id",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context.filter_messages_for_user",
        lambda messages, *_args, **_kwargs: messages,
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context.message_templates.list",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context.list_active_agents_for_mentions",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context.crm_service.get_agent_team_options",
        lambda *_args, **_kwargs: {"agents": [], "agent_labels": {}, "teams": []},
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context.conversation_macros.list",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context.conversation_macros.list_for_agent",
        lambda *_args, **_kwargs: [],
    )

    context = build_inbox_conversation_detail_context(
        None,
        conversation_id="conv-1",
        current_user={"person_id": "person-1"},
        current_roles=[],
    )

    assert context["conversation_attribution"] == {
        "source": "ADS",
        "ad_id": "ad-1",
        "campaign_id": "camp-1",
        "ctwa_clid": "clid-1",
        "source_url": "https://m.me/example",
    }


def test_build_inbox_conversation_detail_context_omits_raw_attribution_when_missing(monkeypatch):
    thread = SimpleNamespace(
        kind="success",
        conversation=SimpleNamespace(id="conv-2", status=ConversationStatus.open, metadata_={}),
        messages=[
            SimpleNamespace(
                id="msg-1",
                created_at=datetime.now(UTC),
            )
        ],
    )

    monkeypatch.setattr(
        "app.services.crm.inbox.thread.load_conversation_thread",
        lambda *_args, **_kwargs: thread,
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context.format_conversation_for_template",
        lambda *_args, **_kwargs: {"id": "conv-2"},
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context.enrich_formatted_conversations_with_labels",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context.format_message_for_template",
        lambda *_args, **_kwargs: {
            "timestamp": datetime.now(UTC),
            "id": "msg-1",
        },
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context._load_assignment_activity",
        lambda *_args, **_kwargs: ([], None),
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context.get_conversation_csat_event",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context.get_current_agent_id",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context.filter_messages_for_user",
        lambda messages, *_args, **_kwargs: messages,
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context.message_templates.list",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context.list_active_agents_for_mentions",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context.crm_service.get_agent_team_options",
        lambda *_args, **_kwargs: {"agents": [], "agent_labels": {}, "teams": []},
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context.conversation_macros.list",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context.conversation_macros.list_for_agent",
        lambda *_args, **_kwargs: [],
    )

    context = build_inbox_conversation_detail_context(
        None,
        conversation_id="conv-2",
        current_user={"person_id": "person-1"},
        current_roles=[],
    )

    assert context["conversation_attribution"] is None


def test_build_inbox_page_context_passes_requested_offset(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_load_inbox_list(_db, **kwargs):
        captured.update(kwargs)
        return InboxListResult(
            conversations_raw=[],
            comment_items=[],
            channel_enum=None,
            status_enum=None,
            include_comments=False,
            target_is_comment=False,
            offset=kwargs["offset"],
            limit=kwargs["limit"],
            has_more=False,
            next_offset=None,
        )

    monkeypatch.setattr("app.services.crm.inbox.page_context.get_current_agent_id", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context.resolve_company_time_prefs",
        lambda _db: ("UTC", "%Y-%m-%d", "%H:%M", "mon"),
    )
    monkeypatch.setattr("app.services.crm.inbox.page_context.load_inbox_list", fake_load_inbox_list)
    monkeypatch.setattr("app.services.crm.inbox.page_context.load_inbox_stats", lambda *_args, **_kwargs: ({}, {}))
    monkeypatch.setattr("app.services.crm.inbox.page_context.get_assignment_counts", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("app.services.crm.inbox.page_context.get_email_channel_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.services.crm.inbox.page_context.list_channel_targets", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.services.crm.inbox.page_context.list_comment_inboxes", lambda *_args, **_kwargs: ([], []))
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context.crm_service.get_agent_team_options",
        lambda *_args, **_kwargs: {"agents": [], "teams": [], "agent_labels": {}, "agent_availability": {}},
    )
    monkeypatch.setattr("app.services.crm.inbox.page_context.message_templates.list", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.services.crm.inbox.page_context.resolve_value", lambda *_args, **_kwargs: 5)
    monkeypatch.setattr("app.services.crm.inbox.page_context.conversation_macros.list", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context.conversation_macros.list_for_agent",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr("app.services.crm.inbox.page_context.get_introduction_template", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        "app.services.crm.inbox.page_context.render_introduction_template",
        lambda *_args, **_kwargs: "",
    )

    context = _run_async(
        build_inbox_page_context(
            SimpleNamespace(),
            current_user={"person_id": ""},
            sidebar_stats={},
            csrf_token="csrf",
            query_params={},
            offset=100,
            limit=50,
            page=3,
        )
    )

    assert captured["offset"] == 100
    assert context["conversations_page"] == 3
