"""Inbox listing helpers for CRM inbox."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.crm.enums import ChannelType, ConversationStatus
from app.services.crm.inbox.queries import list_inbox_conversations
from app.services.crm.inbox.comments_context import (
    build_comment_list_items,
    load_comments_context,
)


@dataclass(frozen=True)
class InboxListResult:
    conversations_raw: list[tuple[Any, Any, int]]
    comment_items: list[dict]
    channel_enum: ChannelType | None
    status_enum: ConversationStatus | None
    include_comments: bool
    target_is_comment: bool


async def load_inbox_list(
    db: Session,
    *,
    channel: str | None,
    status: str | None,
    search: str | None,
    assignment: str | None,
    assigned_person_id: str | None,
    target_id: str | None,
    include_thread: bool = False,
    fetch_comments: bool = False,
) -> InboxListResult:
    channel_enum = None
    status_enum = None
    if channel:
        try:
            channel_enum = ChannelType(channel)
        except ValueError:
            channel_enum = None
    if status:
        try:
            status_enum = ConversationStatus(status)
        except ValueError:
            status_enum = None

    exclude_superseded = status != ConversationStatus.resolved.value if status else True
    assignment_filter = (assignment or "").strip().lower()
    target_prefix = (target_id or "").strip()
    target_is_comment = target_prefix.startswith("fb:") or target_prefix.startswith("ig:")
    include_comments = not channel and assignment_filter != "assigned" and (status_enum is None)
    if target_is_comment:
        include_comments = True
    if include_comments and not target_is_comment and target_prefix:
        include_comments = False

    conversations_raw: list[tuple[Any, Any, int]] = []
    if not target_is_comment:
        conversations_raw = list_inbox_conversations(
            db,
            channel=channel_enum,
            status=status_enum,
            search=search,
            assignment=assignment,
            assigned_person_id=assigned_person_id,
            channel_target_id=target_id,
            exclude_superseded_resolved=exclude_superseded,
            limit=50,
        )

    comment_items: list[dict] = []
    if include_comments:
        context = await load_comments_context(
            db,
            search=search,
            comment_id=None,
            fetch=fetch_comments,
            target_id=target_id,
            include_thread=include_thread,
        )
        comment_items = build_comment_list_items(
            grouped_comments=context.grouped_comments,
            search=search,
            target_id=target_id,
            include_inbox_label=True,
        )

    return InboxListResult(
        conversations_raw=conversations_raw,
        comment_items=comment_items,
        channel_enum=channel_enum,
        status_enum=status_enum,
        include_comments=include_comments,
        target_is_comment=target_is_comment,
    )
