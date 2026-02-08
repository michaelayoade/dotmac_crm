"""Comments context helpers for CRM inbox."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from urllib.parse import quote
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.schemas.settings import DomainSettingUpdate
from app.services import domain_settings as domain_settings_service
from app.services import meta_pages as meta_pages_service
from app.services import settings_spec
from app.services.crm.inbox import cache as inbox_cache
from app.services.crm.inbox.search import normalize_search
from app.logging import get_logger
from app.models.domain_settings import SettingValueType
from app.services.crm import comments as comments_service


logger = get_logger(__name__)



def _group_comment_authors(comments: list) -> list[dict]:
    grouped = []
    seen: dict[str, dict] = {}
    for comment in comments:
        author_key = comment.author_id or (comment.author_name or "").strip().lower()
        if not author_key:
            author_key = f"comment:{comment.external_id}"
        key = f"{comment.platform.value}:{author_key}"
        entry = seen.get(key)
        if not entry:
            entry = {
                "comment": comment,
                "comment_ids": [str(comment.id)],
                "comments": [comment],
                "count": 1,
            }
            seen[key] = entry
            grouped.append(entry)
        else:
            entry["count"] += 1
            entry["comment_ids"].append(str(comment.id))
            entry["comments"].append(comment)
    return grouped


def _apply_comment_inbox_labels(db: Session, grouped_comments: list[dict]) -> None:
    page_lookup = {
        page.get("page_id"): page.get("name")
        for page in meta_pages_service.get_connected_pages(db)
        if page.get("page_id")
    }
    ig_lookup = {
        account.get("account_id"): account.get("username")
        for account in meta_pages_service.get_connected_instagram_accounts(db)
        if account.get("account_id")
    }
    for entry in grouped_comments:
        comment = entry.get("comment")
        if not comment:
            continue
        if comment.platform.value == "facebook":
            entry["inbox_label"] = page_lookup.get(comment.source_account_id) or "Facebook Comments"
        elif comment.platform.value == "instagram":
            entry["inbox_label"] = ig_lookup.get(comment.source_account_id) or "Instagram Comments"
        else:
            entry["inbox_label"] = "Comments"


def build_comment_list_items(
    *,
    grouped_comments: list[dict],
    search: str | None,
    target_id: str | None,
    include_inbox_label: bool = True,
) -> list[dict]:
    items: list[dict] = []
    target_prefix = (target_id or "").strip()
    target_filter = None
    if target_prefix.startswith("fb:"):
        target_filter = ("facebook", target_prefix[3:])
    elif target_prefix.startswith("ig:"):
        target_filter = ("instagram", target_prefix[3:])
    for entry in grouped_comments:
        comment = entry.get("comment")
        if not comment:
            continue
        if target_filter:
            platform, account_id = target_filter
            if comment.platform.value != platform:
                continue
            if account_id and comment.source_account_id != account_id:
                continue
        created_at = comment.created_time or comment.created_at
        inbox_label = entry.get("inbox_label") if include_inbox_label else None
        href = f"/admin/crm/inbox?channel=comments&comment_id={comment.id}"
        if target_id:
            href += f"&target_id={quote(target_id, safe='')}"
        if search:
            href += f"&search={quote(search, safe='')}"
        items.append(
            {
                "kind": "comment",
                "id": str(comment.id),
                "comment_id": str(comment.id),
                "platform": comment.platform.value,
                "author_name": comment.author_name or "Unknown",
                "preview": comment.message or "No message text",
                "created_at": created_at,
                "last_message_at": created_at,
                "inbox_label": inbox_label,
                "href": href,
            }
        )
    return items


def list_comment_inboxes(db: Session) -> tuple[list[dict], list[dict]]:
    facebook_comment_inboxes = [
        {
            "target_id": f"fb:{page.get('page_id')}",
            "name": page.get("name"),
            "channel": "facebook",
            "kind": "comments",
        }
        for page in meta_pages_service.get_connected_pages(db)
        if page.get("page_id")
    ]
    instagram_comment_inboxes = [
        {
            "target_id": f"ig:{account.get('account_id')}",
            "name": account.get("username") or account.get("name"),
            "channel": "instagram",
            "kind": "comments",
        }
        for account in meta_pages_service.get_connected_instagram_accounts(db)
        if account.get("account_id")
    ]
    return facebook_comment_inboxes, instagram_comment_inboxes


@dataclass(frozen=True)
class CommentsContext:
    grouped_comments: list[dict]
    selected_comment: object | None
    comment_replies: list


async def load_comments_context(
    db: Session,
    *,
    search: str | None,
    comment_id: str | None,
    fetch: bool = True,
    target_id: str | None = None,
    include_thread: bool = True,
) -> CommentsContext:
    comments = []
    selected_comment = None
    comment_replies = []
    did_sync = False

    if fetch:
        last_sync_raw = settings_spec.resolve_value(
            db, SettingDomain.comms, "comments_last_sync_at"
        )
        should_sync = True
        if isinstance(last_sync_raw, str) and last_sync_raw.strip():
            try:
                last_sync = datetime.fromisoformat(last_sync_raw.strip())
                if last_sync.tzinfo is None:
                    last_sync = last_sync.replace(tzinfo=timezone.utc)
                should_sync = (datetime.now(timezone.utc) - last_sync).total_seconds() > 120
            except ValueError:
                should_sync = True
        if should_sync:
            try:
                await comments_service.fetch_and_store_social_comments(db)
                did_sync = True
                domain_settings_service.DomainSettings(SettingDomain.comms).upsert_by_key(
                    db,
                    "comments_last_sync_at",
                    DomainSettingUpdate(
                        value_type=SettingValueType.string,
                        value_text=datetime.now(timezone.utc).isoformat(),
                    ),
                )
            except Exception as exc:
                logger.info("crm_inbox_comments_fetch_failed %s", exc)
    if did_sync:
        inbox_cache.invalidate_comments()

    normalized_search = normalize_search(search)
    list_cache_key = inbox_cache.build_comments_list_key(normalized_search)
    cached_comments = inbox_cache.get(list_cache_key)
    if cached_comments is not None:
        comments = cached_comments
    else:
        comments = comments_service.list_social_comments(db, search=normalized_search, limit=50)
        inbox_cache.set(list_cache_key, comments, inbox_cache.COMMENTS_LIST_TTL_SECONDS)

    target_filter = None
    target_prefix = (target_id or "").strip()
    if target_prefix.startswith("fb:"):
        target_filter = ("facebook", target_prefix[3:])
    elif target_prefix.startswith("ig:"):
        target_filter = ("instagram", target_prefix[3:])
    if target_filter:
        platform, account_id = target_filter
        comments = [
            comment
            for comment in comments
            if comment.platform.value == platform
            and (not account_id or comment.source_account_id == account_id)
        ]
    grouped_comments = _group_comment_authors(comments)
    _apply_comment_inbox_labels(db, grouped_comments)
    if comment_id:
        selected_comment = next(
            (comment for comment in comments if str(comment.id) == str(comment_id)),
            None,
        )
        if not selected_comment:
            selected_comment = comments_service.get_social_comment(db, comment_id)
            if (
                selected_comment
                and target_filter
                and (
                    selected_comment.platform.value != target_filter[0]
                    or (
                        target_filter[1]
                        and selected_comment.source_account_id != target_filter[1]
                    )
                )
            ):
                selected_comment = None
    if not selected_comment and comments:
        selected_comment = comments[0]
    if selected_comment and include_thread:
        thread_cache_key = inbox_cache.build_comment_thread_key(str(selected_comment.id))
        cached_replies = inbox_cache.get(thread_cache_key)
        if cached_replies is not None:
            comment_replies = cached_replies
        else:
            comment_replies = comments_service.list_social_comment_replies(
                db, str(selected_comment.id)
            )
            inbox_cache.set(
                thread_cache_key,
                comment_replies,
                inbox_cache.COMMENTS_THREAD_TTL_SECONDS,
            )

    return CommentsContext(
        grouped_comments=grouped_comments,
        selected_comment=selected_comment,
        comment_replies=comment_replies,
    )
