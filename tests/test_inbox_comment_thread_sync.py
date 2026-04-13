"""Tests for social comment thread refresh behavior in the inbox."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from starlette.requests import Request

from app.services.crm.inbox.comments_context import CommentsContext
from app.web.admin.crm_inbox_comments import inbox_comments_thread


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/crm/inbox/comments/thread",
            "headers": [],
            "query_string": b"",
        }
    )


async def _build_page_context(*, channel=None, comment_id=None):
    from app.services.crm.inbox.page_context import build_inbox_page_context

    return await build_inbox_page_context(
        None,
        current_user=None,
        sidebar_stats={},
        csrf_token="csrf",
        query_params={},
        channel=channel,
        comment_id=comment_id,
    )


async def test_inbox_comments_thread_route_fetches_latest_comment_data():
    mock_context = CommentsContext(
        grouped_comments=[],
        selected_comment=None,
        comment_replies=[],
        offset=0,
        limit=150,
        has_more=False,
        next_offset=None,
    )

    with patch(
        "app.services.crm.inbox.comments_context.load_comments_context",
        new=AsyncMock(return_value=mock_context),
    ) as mock_load, patch(
        "app.web.admin.crm_inbox_comments.templates.TemplateResponse",
        return_value=SimpleNamespace(),
    ):
        await inbox_comments_thread(
            request=_request(),
            db=None,
            search="alice",
            comment_id="comment-1",
            target_id="ig:123",
        )

    assert mock_load.await_count == 1
    assert mock_load.await_args.kwargs["fetch"] is True


async def test_build_inbox_page_context_fetches_comments_in_comments_mode():
    mock_context = CommentsContext(
        grouped_comments=[],
        selected_comment=None,
        comment_replies=[],
        offset=0,
        limit=150,
        has_more=False,
        next_offset=None,
    )

    with patch(
        "app.services.crm.inbox.page_context.load_comments_context",
        new=AsyncMock(return_value=mock_context),
    ) as mock_load, patch(
        "app.services.crm.inbox.page_context.resolve_company_time_prefs",
        return_value=("UTC", "%Y-%m-%d", "%H:%M", "monday"),
    ), patch(
        "app.services.crm.inbox.page_context.load_inbox_stats",
        return_value=({}, {}),
    ), patch(
        "app.services.crm.inbox.page_context.get_assignment_counts",
        return_value={},
    ), patch(
        "app.services.crm.inbox.page_context.get_email_channel_state",
        return_value={},
    ), patch(
        "app.services.crm.inbox.page_context.list_channel_targets",
        return_value=[],
    ), patch(
        "app.services.crm.inbox.page_context.list_comment_inboxes",
        return_value=([], []),
    ), patch(
        "app.services.crm.inbox.page_context.crm_service.get_agent_team_options",
        return_value={"agents": [], "teams": [], "agent_labels": {}},
    ), patch(
        "app.services.crm.inbox.page_context.resolve_value",
        return_value=5,
    ), patch(
        "app.services.crm.inbox.page_context.conversation_macros.list",
        return_value=[],
    ):
        await _build_page_context(channel="comments", comment_id="comment-1")

    assert mock_load.await_count == 1
    assert mock_load.await_args.kwargs["fetch"] is True


async def test_build_inbox_page_context_fetches_selected_comment_thread_outside_comments_mode():
    mock_listing = SimpleNamespace(
        conversations_raw=[],
        comment_items=[],
        has_more=False,
        next_offset=None,
        limit=150,
    )
    mock_context = CommentsContext(
        grouped_comments=[],
        selected_comment=None,
        comment_replies=[],
        offset=0,
        limit=1,
        has_more=False,
        next_offset=None,
    )

    with patch(
        "app.services.crm.inbox.page_context.load_inbox_list",
        new=AsyncMock(return_value=mock_listing),
    ), patch(
        "app.services.crm.inbox.page_context.load_comments_context",
        new=AsyncMock(return_value=mock_context),
    ) as mock_load, patch(
        "app.services.crm.inbox.page_context.resolve_company_time_prefs",
        return_value=("UTC", "%Y-%m-%d", "%H:%M", "monday"),
    ), patch(
        "app.services.crm.inbox.page_context.load_inbox_stats",
        return_value=({}, {}),
    ), patch(
        "app.services.crm.inbox.page_context.get_assignment_counts",
        return_value={},
    ), patch(
        "app.services.crm.inbox.page_context.get_email_channel_state",
        return_value={},
    ), patch(
        "app.services.crm.inbox.page_context.list_channel_targets",
        return_value=[],
    ), patch(
        "app.services.crm.inbox.page_context.list_comment_inboxes",
        return_value=([], []),
    ), patch(
        "app.services.crm.inbox.page_context.crm_service.get_agent_team_options",
        return_value={"agents": [], "teams": [], "agent_labels": {}},
    ), patch(
        "app.services.crm.inbox.page_context.resolve_value",
        return_value=5,
    ), patch(
        "app.services.crm.inbox.page_context.enrich_formatted_conversations_with_labels",
        return_value=None,
    ), patch(
        "app.services.crm.inbox.page_context.conversation_macros.list",
        return_value=[],
    ):
        await _build_page_context(comment_id="comment-1")

    assert mock_load.await_count == 1
    assert mock_load.await_args.kwargs["fetch"] is True
