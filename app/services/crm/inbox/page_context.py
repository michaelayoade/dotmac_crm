"""Inbox page context builder for admin UI."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.logic import private_note_logic
from app.models.connector import ConnectorType
from app.models.domain_settings import SettingDomain
from app.services import crm as crm_service
from app.services.crm import conversation as conversation_service
from app.services.crm.inbox.agents import get_current_agent_id
from app.services.crm.inbox.comments_context import list_comment_inboxes, load_comments_context
from app.services.crm.inbox.dashboard import load_inbox_stats
from app.services.crm.inbox.formatting import (
    format_contact_for_template,
    format_conversation_for_template,
)
from app.services.crm.inbox.inboxes import get_email_channel_state, list_channel_targets
from app.services.crm.inbox.listing import load_inbox_list
from app.services.crm.inbox.templates import message_templates
from app.services.settings_spec import resolve_value

logger = logging.getLogger(__name__)


async def build_inbox_page_context(
    db: Session,
    *,
    current_user: dict | None,
    sidebar_stats: dict,
    csrf_token: str,
    query_params: Mapping[str, str],
    channel: str | None = None,
    status: str | None = None,
    search: str | None = None,
    assignment: str | None = None,
    target_id: str | None = None,
    conversation_id: str | None = None,
    comment_id: str | None = None,
    offset: int | None = None,
    limit: int | None = None,
    page: int | None = None,
) -> dict:
    page_limit = max(int(limit or 150), 1)
    safe_page = max(int(page or 1), 1)
    safe_offset = max(int(offset or ((safe_page - 1) * page_limit)), 0)
    assigned_person_id = (current_user or {}).get("person_id")
    current_agent_id = get_current_agent_id(db, assigned_person_id) if db else None

    comments_mode = channel == "comments"
    comments: list[dict] = []
    selected_comment = None
    comment_replies: list[dict] = []
    conversations: list[dict] = []
    selected_conversation = None
    messages: list[dict] = []
    contact_details = None

    if comments_mode:
        context = await load_comments_context(
            db,
            search=search,
            comment_id=comment_id,
            offset=safe_offset,
            limit=page_limit,
            fetch=False,
            target_id=target_id,
        )
        comments = context.grouped_comments
        selected_comment = context.selected_comment
        comment_replies = context.comment_replies
        if conversation_id:
            try:
                conv = conversation_service.Conversations.get(db, conversation_id)
                selected_conversation = format_conversation_for_template(conv, db, include_inbox_label=True)
                if conv.contact:
                    contact_details = format_contact_for_template(conv.contact, db)
            except Exception:
                logger.debug("Failed to format contact details for inbox context.", exc_info=True)

    if not comments_mode:
        listing = await load_inbox_list(
            db,
            channel=channel,
            status=status,
            search=search,
            assignment=assignment,
            assigned_person_id=assigned_person_id,
            target_id=target_id,
            offset=0,
            limit=page_limit,
            include_thread=False,
            fetch_comments=False,
        )
        conversations = [
            format_conversation_for_template(
                conv,
                db,
                latest_message=latest_message,
                unread_count=unread_count,
                include_inbox_label=True,
            )
            for conv, latest_message, unread_count in listing.conversations_raw
        ]
        if listing.comment_items:
            conversations = conversations + listing.comment_items

            def _sort_key(item: dict) -> datetime:
                value = item.get("last_message_at")
                if isinstance(value, str) and value:
                    try:
                        parsed = datetime.fromisoformat(value)
                        if parsed.tzinfo is None:
                            parsed = parsed.replace(tzinfo=UTC)
                        return parsed
                    except ValueError:
                        return datetime.min.replace(tzinfo=UTC)
                if isinstance(value, datetime):
                    return value
                return datetime.min.replace(tzinfo=UTC)

            conversations.sort(key=_sort_key, reverse=True)
            conversations = conversations[:page_limit]

        if comment_id:
            comment_context = await load_comments_context(
                db,
                search=search,
                comment_id=comment_id,
                offset=0,
                limit=1,
                fetch=False,
                target_id=target_id,
                include_thread=True,
            )
            selected_comment = comment_context.selected_comment
            comment_replies = comment_context.comment_replies

        if conversation_id:
            try:
                conv = conversation_service.Conversations.get(db, conversation_id)
                selected_conversation = format_conversation_for_template(conv, db, include_inbox_label=True)
            except Exception:
                logger.debug("Failed to format contact sidebar details for inbox context.", exc_info=True)

    stats, channel_stats = load_inbox_stats(db)

    email_channel = get_email_channel_state(db)
    email_inboxes = list_channel_targets(db, ConnectorType.email)
    whatsapp_inboxes = list_channel_targets(db, ConnectorType.whatsapp)
    facebook_inboxes = list_channel_targets(db, ConnectorType.facebook)
    instagram_inboxes = list_channel_targets(db, ConnectorType.instagram)
    facebook_comment_inboxes, instagram_comment_inboxes = list_comment_inboxes(db)

    assignment_options = crm_service.get_agent_team_options(db)
    templates = []
    if db:
        templates = message_templates.list(
            db,
            channel_type=None,
            is_active=True,
            limit=200,
            offset=0,
        )
    notification_auto_dismiss_seconds = resolve_value(
        db, SettingDomain.notification, "crm_inbox_notification_auto_dismiss_seconds"
    )

    selected_comment_id = None
    if selected_comment is not None:
        selected_comment_id = getattr(selected_comment, "id", None)

    return {
        "current_user": current_user,
        "current_agent_id": current_agent_id,
        "sidebar_stats": sidebar_stats,
        "active_page": "inbox",
        "csrf_token": csrf_token,
        "conversations": conversations,
        "conversations_has_more": listing.has_more if not comments_mode else False,
        "conversations_next_offset": listing.next_offset if not comments_mode else None,
        "conversations_limit": listing.limit if not comments_mode else page_limit,
        "conversations_page": (safe_offset // page_limit) + 1 if not comments_mode else 1,
        "conversations_prev_page": (safe_page - 1) if not comments_mode and safe_page > 1 else None,
        "conversations_next_page": ((safe_offset // page_limit) + 2)
        if not comments_mode and listing.has_more
        else None,
        "comments_has_more": context.has_more if comments_mode else False,
        "comments_next_offset": context.next_offset if comments_mode else None,
        "comments_limit": context.limit if comments_mode else page_limit,
        "comments_page": (safe_offset // page_limit) + 1 if comments_mode else 1,
        "comments_prev_page": (safe_page - 1) if comments_mode and safe_page > 1 else None,
        "comments_next_page": ((safe_offset // page_limit) + 2) if comments_mode and context.has_more else None,
        "pagination_limit": page_limit,
        "selected_conversation": selected_conversation,
        "messages": messages,
        "contact_details": contact_details,
        "comments": comments,
        "selected_comment": selected_comment,
        "selected_comment_id": str(selected_comment_id) if selected_comment_id else None,
        "comment_replies": comment_replies,
        "stats": stats,
        "channel_stats": channel_stats,
        "current_channel": channel,
        "current_status": status,
        "current_assignment": assignment,
        "current_target_id": target_id,
        "search": search,
        "email_channel": email_channel,
        "email_inboxes": email_inboxes,
        "whatsapp_inboxes": whatsapp_inboxes,
        "facebook_inboxes": facebook_inboxes,
        "instagram_inboxes": instagram_inboxes,
        "facebook_comment_inboxes": facebook_comment_inboxes,
        "instagram_comment_inboxes": instagram_comment_inboxes,
        "email_setup": query_params.get("email_setup"),
        "email_error": query_params.get("email_error"),
        "email_error_detail": query_params.get("email_error_detail"),
        "new_error": query_params.get("new_error"),
        "new_error_detail": query_params.get("new_error_detail"),
        "reply_error": query_params.get("reply_error"),
        "reply_error_detail": query_params.get("reply_error_detail"),
        "agents": assignment_options.get("agents"),
        "teams": assignment_options.get("teams"),
        "agent_labels": assignment_options.get("agent_labels"),
        "private_note_enabled": private_note_logic.USE_PRIVATE_NOTE_LOGIC_SERVICE,
        "notification_auto_dismiss_seconds": notification_auto_dismiss_seconds,
        "message_templates": templates,
    }
