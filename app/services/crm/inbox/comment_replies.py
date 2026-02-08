"""Comment reply helpers for CRM inbox."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.services.crm import comments as comments_service


@dataclass(frozen=True)
class CommentReplyResult:
    kind: Literal["not_found", "error", "success"]
    error_detail: str | None = None


async def reply_to_social_comment(
    db: Session,
    *,
    comment_id: str,
    message: str,
) -> CommentReplyResult:
    comment = comments_service.get_social_comment(db, comment_id)
    if not comment:
        return CommentReplyResult(kind="not_found")
    try:
        await comments_service.reply_to_social_comment(db, comment, message.strip())
        return CommentReplyResult(kind="success")
    except Exception as exc:
        return CommentReplyResult(kind="error", error_detail=str(exc) or "Reply failed")
