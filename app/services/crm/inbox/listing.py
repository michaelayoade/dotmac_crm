"""Inbox listing helpers for CRM inbox."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session, selectinload

from app.models.crm.conversation import Conversation
from app.models.crm.enums import ChannelType, ConversationPriority, ConversationStatus
from app.models.person import Person
from app.services.common import coerce_uuid
from app.services.crm.inbox import cache as inbox_cache
from app.services.crm.inbox.comments_context import (
    build_comment_list_items,
    load_comments_context,
)
from app.services.crm.inbox.conversation_status import reopen_due_snoozed_conversations  # noqa: F401
from app.services.crm.inbox.queries import list_inbox_conversations
from app.services.crm.inbox.search import normalize_search

INBOX_LIST_CACHE_SCHEMA = 2
DEFAULT_INBOX_PAGE_SIZE = 50


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


def _serialize_conversations_raw(conversations_raw: list[tuple[Any, Any, int, dict | None]]) -> list[dict]:
    serialized: list[dict] = []
    for conv, latest_message, unread_count, failed_outbox in conversations_raw:
        conv_id = getattr(conv, "id", None)
        if conv_id is None:
            continue
        serialized.append(
            {
                "conversation_id": str(conv_id),
                "latest_message": latest_message,
                "unread_count": int(unread_count or 0),
                "failed_outbox": failed_outbox,
            }
        )
    return serialized


def _hydrate_conversations_raw(db: Session, payload: list[dict]) -> list[tuple[Any, Any, int, dict | None]]:
    if not payload:
        return []
    ordered_ids = [str(item.get("conversation_id") or "").strip() for item in payload]
    valid_ids = []
    for raw_id in ordered_ids:
        if not raw_id:
            continue
        try:
            valid_ids.append(coerce_uuid(raw_id))
        except Exception:  # nosec B112 - skip invalid cached IDs
            continue
    if not valid_ids:
        return []
    rows = (
        db.query(Conversation)
        .options(
            selectinload(Conversation.contact).selectinload(Person.channels),
            selectinload(Conversation.assignments),
            selectinload(Conversation.tags),
        )
        .filter(Conversation.id.in_(valid_ids))
        .all()
    )
    by_id = {str(conv.id): conv for conv in rows}
    hydrated: list[tuple[Any, Any, int, dict | None]] = []
    for item in payload:
        conv_id = str(item.get("conversation_id") or "").strip()
        conv = by_id.get(conv_id)
        if conv is None:
            continue
        hydrated.append(
            (
                conv,
                item.get("latest_message"),
                int(item.get("unread_count") or 0),
                item.get("failed_outbox") if isinstance(item.get("failed_outbox"), dict) else None,
            )
        )
    return hydrated


async def load_inbox_list(
    db: Session,
    *,
    channel: str | None,
    status: str | None,
    priority: str | None = None,
    outbox_status: str | None,
    search: str | None,
    assignment: str | None,
    assigned_person_id: str | None,
    target_id: str | None,
    filter_agent_id: str | None = None,
    assigned_from: datetime | None = None,
    assigned_to: datetime | None = None,
    sort_by: str | None = None,
    missing: str | None = None,
    offset: int = 0,
    limit: int = DEFAULT_INBOX_PAGE_SIZE,
    include_thread: bool = False,
    fetch_comments: bool = False,
) -> InboxListResult:
    normalized_search = normalize_search(search)
    safe_offset = max(int(offset or 0), 0)
    safe_limit = max(int(limit or 0), 1)
    assignment_filter = (assignment or "").strip().lower()
    actor_sensitive_assignment = assignment_filter in {"assigned", "assigned_to_me", "mine", "my_team"}
    cache_params = {
        "cache_schema": INBOX_LIST_CACHE_SCHEMA,
        "channel": channel,
        "status": status,
        "priority": priority,
        "outbox_status": outbox_status,
        "search": normalized_search,
        "assignment": assignment,
        "assigned_person_id": assigned_person_id if actor_sensitive_assignment else None,
        "target_id": target_id,
        "filter_agent_id": filter_agent_id,
        "assigned_from": assigned_from,
        "assigned_to": assigned_to,
        "sort_by": sort_by,
        "missing": missing,
        "offset": safe_offset,
        "limit": safe_limit,
        "include_thread": include_thread,
        "fetch_comments": fetch_comments,
    }
    cache_key = inbox_cache.build_inbox_list_key(cache_params)
    cached = inbox_cache.get(cache_key)
    if isinstance(cached, dict) and cached.get("schema") == INBOX_LIST_CACHE_SCHEMA:
        conversations_payload = cached.get("conversations_raw")
        comment_items_payload = cached.get("comment_items")
        cached_channel_enum = None
        cached_channel_raw = cached.get("channel_enum")
        if isinstance(cached_channel_raw, str) and cached_channel_raw:
            try:
                cached_channel_enum = ChannelType(cached_channel_raw)
            except ValueError:
                cached_channel_enum = None
        cached_status_enum = None
        cached_status_raw = cached.get("status_enum")
        if isinstance(cached_status_raw, str) and cached_status_raw:
            try:
                cached_status_enum = ConversationStatus(cached_status_raw)
            except ValueError:
                cached_status_enum = None
        return InboxListResult(
            conversations_raw=_hydrate_conversations_raw(
                db,
                conversations_payload if isinstance(conversations_payload, list) else [],
            ),
            comment_items=comment_items_payload if isinstance(comment_items_payload, list) else [],
            channel_enum=cached_channel_enum,
            status_enum=cached_status_enum,
            include_comments=bool(cached.get("include_comments")),
            target_is_comment=bool(cached.get("target_is_comment")),
            offset=int(cached.get("offset") or safe_offset),
            limit=int(cached.get("limit") or safe_limit),
            has_more=bool(cached.get("has_more")),
            next_offset=(int(cached["next_offset"]) if isinstance(cached.get("next_offset"), int) else None),
        )

    channel_enum = None
    status_enum = None
    status_enums = None
    priority_enum = None
    outbox_status_filter = (outbox_status or "").strip().lower() or None
    if outbox_status_filter not in {"failed"}:
        outbox_status_filter = None
    if channel:
        try:
            channel_enum = ChannelType(channel)
        except ValueError:
            channel_enum = None
    if priority:
        try:
            priority_enum = ConversationPriority(priority)
        except ValueError:
            priority_enum = None
    if status:
        if status == "needs_action":
            status_enums = [ConversationStatus.open, ConversationStatus.snoozed]
        else:
            try:
                status_enum = ConversationStatus(status)
            except ValueError:
                status_enum = None

    exclude_superseded = status != ConversationStatus.resolved.value if status else True
    target_prefix = (target_id or "").strip()
    target_is_comment = target_prefix.startswith("fb:") or target_prefix.startswith("ig:")
    include_comments = not channel and assignment_filter != "assigned" and (status_enum is None)
    if outbox_status_filter:
        include_comments = False
    if assignment_filter in {"unreplied", "needs_attention"}:
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
            priority=priority_enum,
            outbox_status=outbox_status_filter,
            search=normalized_search,
            assignment=assignment,
            assigned_person_id=assigned_person_id,
            channel_target_id=target_id,
            exclude_superseded_resolved=exclude_superseded,
            filter_agent_id=filter_agent_id,
            assigned_from=assigned_from,
            assigned_to=assigned_to,
            sort_by=sort_by,
            missing=missing,
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
    cache_payload = {
        "schema": INBOX_LIST_CACHE_SCHEMA,
        "conversations_raw": _serialize_conversations_raw(conversations_raw),
        "comment_items": comment_items,
        "channel_enum": channel_enum.value if channel_enum else None,
        "status_enum": status_enum.value if status_enum else None,
        "include_comments": include_comments,
        "target_is_comment": target_is_comment,
        "offset": safe_offset,
        "limit": safe_limit,
        "has_more": has_more,
        "next_offset": next_offset,
    }
    inbox_cache.set(cache_key, cache_payload, inbox_cache.INBOX_LIST_TTL_SECONDS)
    return result
