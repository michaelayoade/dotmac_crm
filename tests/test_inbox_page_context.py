"""Tests for CRM inbox page context builder."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.crm.inbox.page_context import build_inbox_page_context


@pytest.mark.asyncio
async def test_build_inbox_page_context_comments_mode():
    selected_comment = SimpleNamespace(id="comment-1")
    comments_context = SimpleNamespace(
        grouped_comments=[{"id": "comment-1"}],
        selected_comment=selected_comment,
        comment_replies=[{"id": "reply-1"}],
    )
    query_params = {
        "email_setup": "1",
        "email_error": "0",
        "email_error_detail": "",
        "new_error": "1",
        "new_error_detail": "oops",
        "reply_error": "1",
        "reply_error_detail": "fail",
    }

    with (
        patch(
            "app.services.crm.inbox.page_context.load_comments_context",
            return_value=comments_context,
        ) as mock_load_comments,
        patch(
            "app.services.crm.inbox.page_context.load_inbox_list",
        ) as mock_load_list,
        patch(
            "app.services.crm.inbox.page_context.load_inbox_stats",
            return_value=({}, {}),
        ),
        patch(
            "app.services.crm.inbox.page_context.get_email_channel_state",
            return_value={},
        ),
        patch(
            "app.services.crm.inbox.page_context.list_channel_targets",
            return_value=[],
        ),
        patch(
            "app.services.crm.inbox.page_context.list_comment_inboxes",
            return_value=([], []),
        ),
        patch(
            "app.services.crm.inbox.page_context.crm_service.get_agent_team_options",
            return_value={"agents": [], "teams": [], "agent_labels": {}},
        ),
        patch(
            "app.services.crm.inbox.page_context.resolve_value",
            return_value=30,
        ),
    ):
        context = await build_inbox_page_context(
            None,
            current_user={"person_id": "person-1"},
            sidebar_stats={},
            csrf_token="csrf",
            query_params=query_params,
            channel="comments",
            status=None,
            search=None,
            assignment=None,
            target_id=None,
            conversation_id=None,
            comment_id="comment-1",
        )

    assert context["current_channel"] == "comments"
    assert context["comments"] == [{"id": "comment-1"}]
    assert context["selected_comment_id"] == "comment-1"
    assert context["comment_replies"] == [{"id": "reply-1"}]
    assert context["email_setup"] == "1"
    assert context["new_error_detail"] == "oops"
    assert context["reply_error_detail"] == "fail"
    mock_load_comments.assert_called_once()
    mock_load_list.assert_not_called()


@pytest.mark.asyncio
async def test_build_inbox_page_context_conversations_mode():
    listing = SimpleNamespace(
        conversations_raw=[
            ("conv-1", {"last_message_at": "2025-01-02T00:00:00+00:00"}, 0),
            ("conv-2", {"last_message_at": "2025-01-01T00:00:00+00:00"}, 1),
        ],
        comment_items=[{"id": "comment-2", "kind": "comment", "last_message_at": None}],
    )

    def _format_conv(conv, db, **kwargs):
        conv_id = getattr(conv, "id", conv)
        if conv_id == "conv-1":
            return {"id": "conv-1", "kind": "conversation", "last_message_at": "2025-01-02T00:00:00+00:00"}
        return {"id": "conv-2", "kind": "conversation", "last_message_at": "2025-01-01T00:00:00+00:00"}

    with (
        patch(
            "app.services.crm.inbox.page_context.load_inbox_list",
            return_value=listing,
        ) as mock_load_list,
        patch(
            "app.services.crm.inbox.page_context.format_conversation_for_template",
            side_effect=_format_conv,
        ),
        patch(
            "app.services.crm.inbox.page_context.conversation_service.Conversations.get",
            return_value=SimpleNamespace(contact=None, id="conv-1"),
        ),
        patch(
            "app.services.crm.inbox.page_context.load_inbox_stats",
            return_value=({}, {}),
        ),
        patch(
            "app.services.crm.inbox.page_context.get_email_channel_state",
            return_value={},
        ),
        patch(
            "app.services.crm.inbox.page_context.list_channel_targets",
            return_value=[],
        ),
        patch(
            "app.services.crm.inbox.page_context.list_comment_inboxes",
            return_value=([], []),
        ),
        patch(
            "app.services.crm.inbox.page_context.crm_service.get_agent_team_options",
            return_value={"agents": [], "teams": [], "agent_labels": {}},
        ),
        patch(
            "app.services.crm.inbox.page_context.resolve_value",
            return_value=15,
        ),
    ):
        context = await build_inbox_page_context(
            None,
            current_user={"person_id": "person-1"},
            sidebar_stats={},
            csrf_token="csrf",
            query_params={},
            channel=None,
            status=None,
            search=None,
            assignment=None,
            target_id=None,
            conversation_id=None,
            comment_id=None,
        )

    assert context["conversations"]
    assert context["selected_conversation"]["id"] == "conv-1"
    assert context["notification_auto_dismiss_seconds"] == 15
    mock_load_list.assert_called_once()
