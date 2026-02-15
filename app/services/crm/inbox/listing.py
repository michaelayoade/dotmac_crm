"""Inbox listing helpers for CRM inbox."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.crm.enums import ChannelType, ConversationStatus
from app.services.crm.inbox import cache as inbox_cache
from app.services.crm.inbox.comments_context import (
    build_comment_list_items,
    load_comments_context,
)
from app.services.crm.inbox.queries import list_inbox_conversations
from app.services.crm.inbox.search import normalize_search


@dataclass(frozen=True)
class InboxListResult:
    conversations_raw: list[tuple[Any, Any, int, dict | None]]
    comment_items: list[dict]
    channel_enum: ChannelType | None
    status_enum: ConversationStatus | None
    include_comments: bool
    target_is_comment: bool
    offset: int
    limit: int
    has_more: bool
    next_offset: int | None


async def load_inbox_list(
    db: Session,
    *,
    channel: str | None,
    status: str | None,
    outbox_status: str | None,
    search: str | None,
    assignment: str | None,
    assigned_person_id: str | None,
    target_id: str | None,
    offset: int = 0,
    limit: int = 150,
    include_thread: bool = False,
    fetch_comments: bool = False,
) -> InboxListResult:
    normalized_search = normalize_search(search)
    safe_offset = max(int(offset or 0), 0)
    safe_limit = max(int(limit or 0), 1)
    cache_params = {
        "channel": channel,
        "status": status,
        "outbox_status": outbox_status,
        "search": normalized_search,
        "assignment": assignment,
        "assigned_person_id": assigned_person_id,
        "target_id": target_id,
        "offset": safe_offset,
        "limit": safe_limit,
        "include_thread": include_thread,
        "fetch_comments": fetch_comments,
    }
    cache_key = inbox_cache.build_inbox_list_key(cache_params)
    cached = inbox_cache.get(cache_key)
    if cached is not None:
        return cached

    channel_enum = None
    status_enum = None
    status_enums = None
    outbox_status_filter = (outbox_status or "").strip().lower() or None
    if outbox_status_filter not in {"failed"}:
        outbox_status_filter = None
    if channel:
        try:
            channel_enum = ChannelType(channel)
        except ValueError:
            channel_enum = None
    if status:
        if status == "needs_action":
            status_enums = [ConversationStatus.open, ConversationStatus.snoozed]
        else:
            try:
                status_enum = ConversationStatus(status)
            except ValueError:
                status_enum = None

    exclude_superseded = status != ConversationStatus.resolved.value if status else True
    assignment_filter = (assignment or "").strip().lower()
    target_prefix = (target_id or "").strip()
    target_is_comment = target_prefix.startswith("fb:") or target_prefix.startswith("ig:")
    include_comments = not channel and assignment_filter != "assigned" and (status_enum is None)
    if outbox_status_filter:
        include_comments = False
    if target_is_comment:
        include_comments = True
    if include_comments and not target_is_comment and target_prefix:
        include_comments = False
    if safe_offset > 0:
        include_comments = False

    conversations_raw: list[tuple[Any, Any, int, dict | None]] = []
    has_more = False
    next_offset: int | None = None
    if not target_is_comment:
        conversations_raw = list_inbox_conversations(
            db,
            channel=channel_enum,
            status=status_enum,
            statuses=status_enums,
            outbox_status=outbox_status_filter,
            search=normalized_search,
            assignment=assignment,
            assigned_person_id=assigned_person_id,
            channel_target_id=target_id,
            exclude_superseded_resolved=exclude_superseded,
            limit=safe_limit + 1,
            offset=safe_offset,
        )
        if len(conversations_raw) > safe_limit:
            has_more = True
            conversations_raw = conversations_raw[:safe_limit]
            next_offset = safe_offset + safe_limit

    comment_items: list[dict] = []
    if include_comments:
        context = await load_comments_context(
            db,
            search=normalized_search,
            comment_id=None,
            fetch=fetch_comments,
            target_id=target_id,
            include_thread=include_thread,
        )
        comment_items = build_comment_list_items(
            grouped_comments=context.grouped_comments,
            search=normalized_search,
            target_id=target_id,
            include_inbox_label=True,
        )

    result = InboxListResult(
        conversations_raw=conversations_raw,
        comment_items=comment_items,
        channel_enum=channel_enum,
        status_enum=status_enum,
        include_comments=include_comments,
        target_is_comment=target_is_comment,
        offset=safe_offset,
        limit=safe_limit,
        has_more=has_more,
        next_offset=next_offset,
    )
    inbox_cache.set(cache_key, result, inbox_cache.INBOX_LIST_TTL_SECONDS)
    return result
