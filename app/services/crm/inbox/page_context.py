"""Inbox page context builder for admin UI."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping

from sqlalchemy.orm import Session

from app.models.connector import ConnectorType
from app.models.domain_settings import SettingDomain
from app.services.crm import conversation as conversation_service
from app.services.crm.inbox.agents import get_current_agent_id
from app.services.crm.inbox.comments_context import load_comments_context, list_comment_inboxes
from app.services.crm.inbox.dashboard import load_inbox_stats
from app.services.crm.inbox.formatting import (
    format_contact_for_template,
    format_conversation_for_template,
)
from app.services.crm.inbox.inboxes import get_email_channel_state, list_channel_targets
from app.services.crm.inbox.listing import load_inbox_list
from app.services.settings_spec import resolve_value
from app.logic import private_note_logic
from app.services import crm as crm_service


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
) -> dict:
    assigned_person_id = (current_user or {}).get("person_id")
    current_agent_id = get_current_agent_id(db, assigned_person_id)

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
            fetch=False,
            target_id=target_id,
        )
        comments = context.grouped_comments
        selected_comment = context.selected_comment
        comment_replies = context.comment_replies
        if conversation_id:
            try:
                conv = conversation_service.Conversations.get(db, conversation_id)
                selected_conversation = format_conversation_for_template(
                    conv, db, include_inbox_label=True
                )
                if conv.contact:
                    contact_details = format_contact_for_template(conv.contact, db)
            except Exception:
                pass

    if not comments_mode:
        listing = await load_inbox_list(
            db,
            channel=channel,
            status=status,
            search=search,
            assignment=assignment,
            assigned_person_id=assigned_person_id,
            target_id=target_id,
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
                            parsed = parsed.replace(tzinfo=timezone.utc)
                        return parsed
                    except ValueError:
                        return datetime.min.replace(tzinfo=timezone.utc)
                if isinstance(value, datetime):
                    return value
                return datetime.min.replace(tzinfo=timezone.utc)

            conversations.sort(key=_sort_key, reverse=True)
            conversations = conversations[:50]

        target_conv_id = conversation_id
        if not target_conv_id and conversations:
            first_conv = next(
                (entry for entry in conversations if entry.get("kind") != "comment"),
                None,
            )
            if first_conv:
                target_conv_id = first_conv["id"]

        if target_conv_id:
            try:
                conv = conversation_service.Conversations.get(db, target_conv_id)
                selected_conversation = format_conversation_for_template(
                    conv, db, include_inbox_label=True
                )
            except Exception:
                pass

    stats, channel_stats = load_inbox_stats(db)

    email_channel = get_email_channel_state(db)
    email_inboxes = list_channel_targets(db, ConnectorType.email)
    whatsapp_inboxes = list_channel_targets(db, ConnectorType.whatsapp)
    facebook_inboxes = list_channel_targets(db, ConnectorType.facebook)
    instagram_inboxes = list_channel_targets(db, ConnectorType.instagram)
    facebook_comment_inboxes, instagram_comment_inboxes = list_comment_inboxes(db)

    assignment_options = crm_service.get_agent_team_options(db)
    notification_auto_dismiss_seconds = resolve_value(
        db, SettingDomain.notification, "crm_inbox_notification_auto_dismiss_seconds"
    )

    return {
        "current_user": current_user,
        "current_agent_id": current_agent_id,
        "sidebar_stats": sidebar_stats,
        "active_page": "inbox",
        "csrf_token": csrf_token,
        "conversations": conversations,
        "selected_conversation": selected_conversation,
        "messages": messages,
        "contact_details": contact_details,
        "comments": comments,
        "selected_comment": selected_comment,
        "selected_comment_id": str(selected_comment.id) if selected_comment else None,
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
    }
