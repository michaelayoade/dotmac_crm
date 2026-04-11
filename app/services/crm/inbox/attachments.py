"""Attachment fetch helpers for CRM inbox."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.crm.conversation import Message
from app.models.crm.enums import ChannelType
from app.models.domain_settings import SettingDomain
from app.services import meta_oauth
from app.services.common import coerce_uuid
from app.services.settings_spec import resolve_value

logger = logging.getLogger(__name__)

# Cache of attachment IDs that returned 400-level errors from Meta.
# Maps attachment_id -> (status_code, timestamp).  Entries expire after 1 hour
# so we can retry in case the issue was transient (e.g. token rotation).
_FAILED_ATTACHMENT_CACHE: dict[str, tuple[int, float]] = {}
_FAILED_CACHE_TTL = 3600  # seconds


@dataclass(frozen=True)
class AttachmentFetchResult:
    kind: Literal["not_found", "redirect", "content"]
    content: bytes | None = None
    content_type: str | None = None
    redirect_url: str | None = None


def fetch_inbox_attachment(
    db: Session,
    message_id: str,
    attachment_index: int,
) -> AttachmentFetchResult:
    try:
        message_uuid = coerce_uuid(message_id)
    except Exception:
        return AttachmentFetchResult(kind="not_found")

    message = db.get(Message, message_uuid)
    if not message:
        return AttachmentFetchResult(kind="not_found")

    metadata = message.metadata_ if isinstance(message.metadata_, dict) else {}
    attachments = metadata.get("attachments")
    if not isinstance(attachments, list):
        return AttachmentFetchResult(kind="not_found")
    if attachment_index < 0 or attachment_index >= len(attachments):
        return AttachmentFetchResult(kind="not_found")
    meta_attachment = attachments[attachment_index]
    if not isinstance(meta_attachment, dict):
        return AttachmentFetchResult(kind="not_found")

    payload_value = meta_attachment.get("payload")
    payload = payload_value if isinstance(payload_value, dict) else {}
    attachment_id = payload.get("attachment_id") or payload.get("id") or meta_attachment.get("id")
    url = payload.get("url") or meta_attachment.get("url")

    # Skip re-fetching attachments that already failed with a client error from Meta.
    if attachment_id and attachment_id in _FAILED_ATTACHMENT_CACHE:
        cached_status, cached_at = _FAILED_ATTACHMENT_CACHE[attachment_id]
        if time.monotonic() - cached_at < _FAILED_CACHE_TTL:
            logger.debug(
                "crm_inbox_attachment_cached_failure attachment_id=%s status=%s",
                attachment_id,
                cached_status,
            )
            return AttachmentFetchResult(kind="not_found")
        del _FAILED_ATTACHMENT_CACHE[attachment_id]
    if url and not url.startswith(("http://", "https://")):
        return AttachmentFetchResult(kind="redirect", redirect_url=url)

    token = None
    config = None
    if message.channel_type == ChannelType.whatsapp:
        from app.services.crm.inbox_connectors import (
            _resolve_connector_config,
            _resolve_integration_target,
        )

        if message.channel_target_id:
            target = _resolve_integration_target(
                db,
                ChannelType.whatsapp,
                str(message.channel_target_id),
            )
        else:
            target = _resolve_integration_target(db, ChannelType.whatsapp, None)
        config = _resolve_connector_config(db, target, ChannelType.whatsapp) if target else None
        if config and config.auth_config:
            token = config.auth_config.get("token") or config.auth_config.get("access_token")
    elif message.channel_type == ChannelType.instagram_dm:
        ig_account_id = metadata.get("instagram_account_id")
        if ig_account_id:
            token = meta_oauth.get_token_for_instagram(db, str(ig_account_id))
    elif message.channel_type == ChannelType.facebook_messenger:
        page_id = metadata.get("page_id")
        if page_id:
            token = meta_oauth.get_token_for_page(db, str(page_id))

    if message.channel_type == ChannelType.whatsapp:
        if not token or not attachment_id:
            return AttachmentFetchResult(kind="not_found")
    else:
        if not token or not getattr(token, "access_token", None) or not attachment_id:
            return AttachmentFetchResult(kind="not_found")

    try:
        version = resolve_value(db, SettingDomain.comms, "meta_graph_api_version")
        if not version:
            version = settings.meta_graph_api_version
        if message.channel_type == ChannelType.whatsapp and config and config.base_url:
            base_url = config.base_url
        else:
            base_url = f"https://graph.facebook.com/{version}"

        media_url = None
        if url and url.startswith(("http://", "https://")):
            media_url = url

        if message.channel_type == ChannelType.whatsapp:
            if not media_url:
                response = httpx.get(
                    f"{base_url.rstrip('/')}/{attachment_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10,
                )
                if response.status_code >= 400:
                    _cache_and_log_failure(
                        attachment_id, response, message_id, message.channel_type,
                    )
                    return AttachmentFetchResult(kind="not_found")
                payload = response.json() if response.content else {}
                media_url = payload.get("url") or payload.get("media_url")
            if not media_url:
                return AttachmentFetchResult(kind="not_found")
            media_response = httpx.get(
                media_url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
        else:
            if not media_url:
                response = httpx.get(
                    f"{base_url.rstrip('/')}/{attachment_id}",
                    params={"access_token": token.access_token, "fields": "url,media_url"},
                    timeout=10,
                )
                if response.status_code >= 400:
                    _cache_and_log_failure(
                        attachment_id, response, message_id, message.channel_type,
                    )
                    return AttachmentFetchResult(kind="not_found")
                payload = response.json() if response.content else {}
                media_url = payload.get("url") or payload.get("media_url")
            if not media_url:
                return AttachmentFetchResult(kind="not_found")
            media_response = httpx.get(
                media_url,
                params={"access_token": token.access_token},
                timeout=10,
            )
        if media_response.status_code >= 400:
            _cache_and_log_failure(
                attachment_id, media_response, message_id, message.channel_type,
            )
            return AttachmentFetchResult(kind="not_found")
    except httpx.HTTPError:
        return AttachmentFetchResult(kind="not_found")

    content_type = media_response.headers.get("content-type") or "application/octet-stream"
    return AttachmentFetchResult(
        kind="content",
        content=media_response.content,
        content_type=content_type,
    )


def _cache_and_log_failure(
    attachment_id: str | None,
    response: httpx.Response,
    message_id: str,
    channel_type: ChannelType,
) -> None:
    """Log the Meta API error body and cache the failure to avoid repeated calls."""
    error_body = response.text[:500] if response.text else "(empty)"
    logger.warning(
        "crm_inbox_attachment_fetch_failed message_id=%s attachment_id=%s "
        "channel=%s status=%s body=%s",
        message_id,
        attachment_id,
        channel_type.value if channel_type else "unknown",
        response.status_code,
        error_body,
    )
    if attachment_id and 400 <= response.status_code < 500:
        # Cap cache size to prevent unbounded growth from many unique failures.
        if len(_FAILED_ATTACHMENT_CACHE) >= 1000:
            # Evict the oldest entry.
            oldest_key = min(_FAILED_ATTACHMENT_CACHE, key=lambda k: _FAILED_ATTACHMENT_CACHE[k][1])
            del _FAILED_ATTACHMENT_CACHE[oldest_key]
        _FAILED_ATTACHMENT_CACHE[attachment_id] = (response.status_code, time.monotonic())
