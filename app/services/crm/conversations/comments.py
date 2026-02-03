from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.crm.comments import (
    SocialComment,
    SocialCommentPlatform,
    SocialCommentReply,
)
import httpx

from app.services import meta_pages
from app.services.crm import contact as contact_service
from app.models.crm.enums import ChannelType as CrmChannelType
from app.services.common import coerce_uuid


def _parse_meta_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate.replace("Z", "+00:00")
    if candidate.endswith("+0000"):
        candidate = candidate[:-5] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _ensure_comment_contact(db: Session, payload: dict[str, Any]) -> None:
    platform = payload.get("platform")
    if not platform:
        return
    address = payload.get("author_id") or payload.get("author_name")
    if not address:
        return
    channel_type = (
        CrmChannelType.facebook_messenger
        if platform == SocialCommentPlatform.facebook
        else CrmChannelType.instagram_dm
    )
    try:
        contact_service.get_or_create_contact_by_channel(
            db,
            channel_type,
            str(address),
            payload.get("author_name"),
        )
    except Exception:
        return


def _upsert_comment(db: Session, payload: dict[str, Any]) -> SocialComment:
    _ensure_comment_contact(db, payload)
    existing = (
        db.query(SocialComment)
        .filter(SocialComment.platform == payload["platform"])
        .filter(SocialComment.external_id == payload["external_id"])
        .first()
    )
    if existing:
        for key, value in payload.items():
            setattr(existing, key, value)
        return existing
    comment = SocialComment(**payload)
    db.add(comment)
    return comment


def _upsert_comment_reply(
    db: Session,
    parent_comment: SocialComment,
    payload: dict[str, Any],
) -> SocialCommentReply:
    existing = (
        db.query(SocialCommentReply)
        .filter(SocialCommentReply.platform == payload["platform"])
        .filter(SocialCommentReply.external_id == payload["external_id"])
        .first()
    )
    if existing:
        for key, value in payload.items():
            setattr(existing, key, value)
        if existing.comment_id != parent_comment.id:
            existing.comment_id = parent_comment.id
        return existing
    reply = SocialCommentReply(comment_id=parent_comment.id, **payload)
    db.add(reply)
    return reply


def upsert_social_comment_reply(
    db: Session,
    platform: SocialCommentPlatform,
    parent_external_id: str | None,
    external_id: str | None,
    message: str | None,
    created_time: datetime | None,
    raw_payload: dict | None,
) -> SocialCommentReply | None:
    if not parent_external_id or not external_id:
        return None
    parent = (
        db.query(SocialComment)
        .filter(SocialComment.platform == platform)
        .filter(SocialComment.external_id == parent_external_id)
        .first()
    )
    if not parent:
        return None
    payload = {
        "platform": platform,
        "external_id": external_id,
        "message": message or "",
        "created_time": created_time,
        "raw_payload": raw_payload,
        "is_active": True,
    }
    reply = _upsert_comment_reply(db, parent, payload)
    db.commit()
    db.refresh(reply)
    return reply


def upsert_social_comment(
    db: Session,
    platform: SocialCommentPlatform,
    external_id: str,
    external_post_id: str | None,
    source_account_id: str | None,
    author_id: str | None,
    author_name: str | None,
    message: str | None,
    created_time: datetime | None,
    permalink_url: str | None,
    raw_payload: dict | None,
) -> SocialComment | None:
    if not external_id:
        return None
    payload = {
        "platform": platform,
        "external_id": external_id,
        "external_post_id": external_post_id,
        "source_account_id": source_account_id,
        "author_id": author_id,
        "author_name": author_name,
        "message": message,
        "created_time": created_time,
        "permalink_url": permalink_url,
        "raw_payload": raw_payload,
    }
    comment = _upsert_comment(db, payload)
    db.commit()
    db.refresh(comment)
    return comment


async def fetch_and_store_social_comments(
    db: Session,
    post_limit: int = 8,
    comment_limit: int = 25,
) -> dict[str, int]:
    fetched = 0
    stored = 0

    for page in meta_pages.get_connected_pages(db):
        page_id = page.get("page_id")
        if not page_id:
            continue
        posts = await meta_pages.get_page_posts(db, page_id, limit=post_limit)
        for post in posts:
            post_id = post.get("id")
            if not post_id:
                continue
            comments = await meta_pages.get_post_comments(
                db, page_id, post_id, limit=comment_limit
            )
            for comment in comments:
                if not comment.get("id"):
                    continue
                fetched += 1
                _upsert_comment(
                    db,
                    {
                        "platform": SocialCommentPlatform.facebook,
                        "external_id": str(comment.get("id")),
                        "external_post_id": str(post_id),
                        "source_account_id": str(page_id),
                        "author_id": (comment.get("from") or {}).get("id"),
                        "author_name": (comment.get("from") or {}).get("name"),
                        "message": comment.get("message"),
                        "created_time": _parse_meta_datetime(comment.get("created_time")),
                        "permalink_url": post.get("permalink_url"),
                        "raw_payload": {
                            "post": post,
                            "comment": comment,
                        },
                    },
                )
                stored += 1

    for account in meta_pages.get_connected_instagram_accounts(db):
        ig_account_id = account.get("account_id")
        if not ig_account_id:
            continue
        media_items = await meta_pages.get_instagram_media(
            db, ig_account_id, limit=post_limit
        )
        for media in media_items:
            media_id = media.get("id")
            if not media_id:
                continue
            comments = await meta_pages.get_instagram_media_comments(
                db, ig_account_id, media_id, limit=comment_limit
            )
            for comment in comments:
                if not comment.get("id"):
                    continue
                fetched += 1
                _upsert_comment(
                    db,
                    {
                        "platform": SocialCommentPlatform.instagram,
                        "external_id": str(comment.get("id")),
                        "external_post_id": str(media_id),
                        "source_account_id": str(ig_account_id),
                        "author_id": comment.get("username"),
                        "author_name": comment.get("username"),
                        "message": comment.get("text"),
                        "created_time": _parse_meta_datetime(comment.get("timestamp")),
                        "permalink_url": media.get("permalink"),
                        "raw_payload": {
                            "media": media,
                            "comment": comment,
                        },
                    },
                )
                stored += 1

    if stored:
        db.commit()

    return {"fetched": fetched, "stored": stored}


def list_social_comments(
    db: Session,
    search: str | None = None,
    platform: SocialCommentPlatform | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[SocialComment]:
    query = db.query(SocialComment).filter(SocialComment.is_active.is_(True))
    query = query.filter(SocialComment.external_id.isnot(None)).filter(SocialComment.external_id != "")
    if platform:
        query = query.filter(SocialComment.platform == platform)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.filter(
            or_(
                SocialComment.author_name.ilike(pattern),
                SocialComment.message.ilike(pattern),
            )
        )
    query = query.order_by(
        SocialComment.created_time.desc().nullslast(),
        SocialComment.created_at.desc(),
    )
    return query.offset(offset).limit(limit).all()


def get_social_comment(db: Session, comment_id: str) -> SocialComment | None:
    try:
        comment_uuid = coerce_uuid(comment_id)
    except Exception:
        return None
    return db.query(SocialComment).filter(SocialComment.id == comment_uuid).first()


def list_social_comment_replies(
    db: Session,
    comment_id: str,
    limit: int = 50,
    offset: int = 0,
) -> list[SocialCommentReply]:
    try:
        comment_uuid = coerce_uuid(comment_id)
    except Exception:
        return []
    return (
        db.query(SocialCommentReply)
        .filter(SocialCommentReply.comment_id == comment_uuid)
        .filter(SocialCommentReply.is_active.is_(True))
        .order_by(SocialCommentReply.created_time.desc().nullslast(), SocialCommentReply.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


async def reply_to_social_comment(
    db: Session,
    comment: SocialComment,
    message: str,
) -> SocialCommentReply:
    if not comment.external_id or not comment.source_account_id:
        raise RuntimeError("Missing comment identifiers for reply")
    try:
        if comment.platform == SocialCommentPlatform.facebook:
            result = await meta_pages.reply_to_comment(
                db,
                page_id=comment.source_account_id,
                comment_id=comment.external_id,
                message=message,
            )
            external_id = result.get("id")
        else:
            result = await meta_pages.reply_to_instagram_comment(
                db,
                ig_account_id=comment.source_account_id,
                comment_id=comment.external_id,
                message=message,
            )
            external_id = result.get("id")
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        raise RuntimeError(f"Meta reply failed: {detail}") from exc

    reply = SocialCommentReply(
        comment_id=comment.id,
        platform=comment.platform,
        external_id=str(external_id) if external_id else None,
        message=message,
        created_time=datetime.now(timezone.utc),
        raw_payload=result,
    )
    db.add(reply)
    db.commit()
    db.refresh(reply)
    return reply


class SocialComments:
    @staticmethod
    async def fetch_and_store(db: Session) -> dict:
        return await fetch_and_store_social_comments(db)

    @staticmethod
    def list(
        db: Session,
        search: str | None = None,
        platform: SocialCommentPlatform | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SocialComment]:
        return list_social_comments(
            db,
            search=search,
            platform=platform,
            limit=limit,
            offset=offset,
        )


class SocialCommentReplies:
    @staticmethod
    def list(
        db: Session,
        comment_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SocialCommentReply]:
        return list_social_comment_replies(
            db,
            comment_id=comment_id,
            limit=limit,
            offset=offset,
        )

    @staticmethod
    async def reply(
        db: Session,
        comment: SocialComment,
        message: str,
    ) -> SocialCommentReply:
        return await reply_to_social_comment(db, comment, message)


# Singleton instances
social_comments = SocialComments()
social_comment_replies = SocialCommentReplies()
