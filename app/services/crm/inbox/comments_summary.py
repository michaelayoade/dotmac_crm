"""Comment summary helpers for CRM inbox."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.models.crm.comments import SocialCommentPlatform
from app.services.crm import contact as contact_service
from app.services.crm.inbox.comments_context import _group_comment_authors


def build_comment_summaries(social_comments: list[Any]) -> list[dict]:
    comment_summaries: list[dict] = []
    if not social_comments:
        return comment_summaries
    grouped_comments = _group_comment_authors(social_comments)
    for entry in grouped_comments:
        comment = entry["comment"]
        created_at = comment.created_time or comment.created_at
        platform_label = "Facebook" if comment.platform == SocialCommentPlatform.facebook else "Instagram"
        comment_summaries.append(
            {
                "id": str(comment.id),
                "subject": f"{platform_label} comment",
                "status": "comment",
                "updated_at": created_at.strftime("%Y-%m-%d %H:%M") if created_at else "N/A",
                "preview": comment.message or "No message text",
                "channel": "comments",
                "platform_label": platform_label,
                "comment_count": entry.get("count", 1),
                "older_comments": [
                    {
                        "id": str(older.id),
                        "label": older.created_time.strftime("%b %d, %H:%M") if older.created_time else "View",
                        "href": f"/admin/crm/inbox?comment_id={older.id}",
                    }
                    for older in (entry.get("comments") or [])[1:4]
                ],
                "older_more": max((entry.get("count") or 0) - 4, 0),
                "sort_at": created_at,
                "href": f"/admin/crm/inbox?comment_id={comment.id}",
            }
        )
    return comment_summaries


def merge_recent_conversations_with_comments(
    db,
    person_id: str,
    recent_conversations: list[dict],
    *,
    comment_limit: int = 10,
    limit: int = 5,
) -> list[dict]:
    social_comments = contact_service.get_contact_social_comments(db, person_id, limit=comment_limit)
    comment_summaries = build_comment_summaries(social_comments)
    if not comment_summaries:
        return recent_conversations
    merged = list(recent_conversations) + comment_summaries
    merged.sort(
        key=lambda item: item.get("sort_at") or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    return merged[:limit]
