"""Comment reply helpers for CRM inbox."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.services.crm import comments as comments_service
from app.services.crm.inbox.audit import log_comment_action


@dataclass(frozen=True)
class CommentReplyResult:
    kind: Literal["not_found", "error", "success"]
    error_detail: str | None = None


async def reply_to_social_comment(
    db: Session,
    *,
    comment_id: str,
    message: str,
    actor_id: str | None = None,
) -> CommentReplyResult:
    comment = comments_service.get_social_comment(db, comment_id)
    if not comment:
        return CommentReplyResult(kind="not_found")
    try:
        await comments_service.reply_to_social_comment(db, comment, message.strip())
        log_comment_action(
            db,
            action="reply_comment",
            comment_id=str(comment.id),
            actor_id=actor_id,
        )
        return CommentReplyResult(kind="success")
    except Exception as exc:
        return CommentReplyResult(kind="error", error_detail=str(exc) or "Reply failed")
