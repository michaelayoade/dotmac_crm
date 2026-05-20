"""Read-only diagnostics for recent Meta inbound message attribution."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, joinedload

from app.db import SessionLocal
from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, MessageDirection
from app.models.person import ChannelType as PersonChannelType
from app.models.person import Person, PersonChannel
from app.services.meta_webhooks import _extract_meta_attribution, _is_meta_ad_attribution
from app.services.person_identity import (
    _is_meta_placeholder_name,
    get_meta_profile,
    meta_platform_for_channel,
    preferred_meta_display_name,
)

META_CHANNELS = frozenset(
    {
        ChannelType.instagram_dm,
        ChannelType.facebook_messenger,
        ChannelType.whatsapp,
    }
)
PROVIDER_CHANNELS: dict[str, ChannelType] = {
    "instagram": ChannelType.instagram_dm,
    "facebook": ChannelType.facebook_messenger,
    "whatsapp": ChannelType.whatsapp,
}


def _provider_for_channel(channel_type: ChannelType) -> str:
    if channel_type == ChannelType.instagram_dm:
        return "instagram"
    if channel_type == ChannelType.facebook_messenger:
        return "facebook"
    return "whatsapp"


def _normalize_providers(providers: list[str] | tuple[str, ...] | set[str] | None) -> tuple[str, ...]:
    if not providers:
        return tuple(PROVIDER_CHANNELS)
    normalized: list[str] = []
    for provider in providers:
        candidate = provider.strip().lower()
        if candidate in PROVIDER_CHANNELS and candidate not in normalized:
            normalized.append(candidate)
    return tuple(normalized)


def _normalize_dict(value: object) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, dict) and value else None


def _serialize_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _first_dict_item(items: object) -> dict[str, Any]:
    if not isinstance(items, list) or not items:
        return {}
    first = items[0]
    return dict(first) if isinstance(first, dict) else {}


def _extract_sender_identity(message: Message, person: Person | None) -> dict[str, str | None]:
    metadata = message.metadata_ if isinstance(message.metadata_, dict) else {}
    provider = _provider_for_channel(message.channel_type)
    raw_value = metadata.get("raw")
    raw: dict[str, Any] = raw_value if isinstance(raw_value, dict) else {}
    platform = meta_platform_for_channel(message.channel_type)
    person_profile_value = get_meta_profile(person.metadata_ if person else None, platform) if platform else {}
    person_profile: dict[str, Any] = person_profile_value if isinstance(person_profile_value, dict) else {}
    sender_id = metadata.get("sender_id") if isinstance(metadata.get("sender_id"), str) else None
    sender_username = metadata.get("sender_username") if isinstance(metadata.get("sender_username"), str) else None
    sender_name = metadata.get("sender_name") if isinstance(metadata.get("sender_name"), str) else None

    if provider in {"instagram", "facebook"}:
        raw_sender_value = raw.get("sender")
        raw_sender: dict[str, Any] = raw_sender_value if isinstance(raw_sender_value, dict) else {}
        if sender_id is None:
            raw_sender_id = raw_sender.get("id")
            if isinstance(raw_sender_id, str) and raw_sender_id.strip():
                sender_id = raw_sender_id.strip()
        if sender_username is None:
            raw_username = raw_sender.get("username")
            if isinstance(raw_username, str) and raw_username.strip():
                sender_username = raw_username.strip()
        if sender_name is None:
            raw_name = raw_sender.get("name")
            if isinstance(raw_name, str) and raw_name.strip():
                sender_name = raw_name.strip()
        sender_id = sender_id or person_profile.get("sender_id")
        sender_username = sender_username or person_profile.get("sender_username")
        sender_name = sender_name or person_profile.get("sender_name")
    else:
        raw_contacts: dict[str, Any] = _first_dict_item(raw.get("contacts")) or {}
        raw_messages: dict[str, Any] = _first_dict_item(raw.get("messages")) or {}
        if sender_id is None:
            for candidate in (
                raw_messages.get("from"),
                raw_contacts.get("wa_id"),
            ):
                if isinstance(candidate, str) and candidate.strip():
                    sender_id = candidate.strip()
                    break
        if sender_name is None:
            profile_value = raw_contacts.get("profile")
            profile: dict[str, Any] = profile_value if isinstance(profile_value, dict) else {}
            raw_name = profile.get("name")
            if isinstance(raw_name, str) and raw_name.strip():
                sender_name = raw_name.strip()

    return {
        "sender_id": sender_id,
        "sender_username": sender_username,
        "sender_name": sender_name,
    }


def _extract_raw_attribution(metadata: dict[str, Any]) -> dict[str, Any] | None:
    raw = metadata.get("raw") if isinstance(metadata.get("raw"), dict) else None
    raw_attribution = _extract_meta_attribution(raw, metadata)
    return raw_attribution if raw_attribution else None


def _classify_message(
    *,
    provider: str,
    display_name: str | None,
    raw_attribution: dict[str, Any] | None,
    message_attribution: dict[str, Any] | None,
    conversation_attribution: dict[str, Any] | None,
) -> str:
    raw_has_ads = _is_meta_ad_attribution(raw_attribution)
    stored_has_ads = _is_meta_ad_attribution(message_attribution) or _is_meta_ad_attribution(conversation_attribution)
    if raw_has_ads and not stored_has_ads:
        return "attribution_lost_between_webhook_and_persistence"
    if stored_has_ads:
        return "meta_ads_attributed"
    if provider == "instagram" and _is_meta_placeholder_name(display_name):
        return "missing_identity_fallback"
    if provider == "facebook" and _is_meta_placeholder_name(display_name):
        return "missing_identity_fallback"
    if provider == "instagram":
        return "instagram_organic"
    if provider == "facebook":
        return "facebook_organic"
    return "whatsapp_organic"


def get_recent_meta_message_attribution(
    db: Session,
    limit: int = 20,
    *,
    providers: list[str] | tuple[str, ...] | set[str] | None = None,
) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit), 200))
    selected_providers = _normalize_providers(providers)
    selected_channels = tuple(PROVIDER_CHANNELS[provider] for provider in selected_providers)
    messages = (
        db.query(Message)
        .options(
            joinedload(Message.conversation).joinedload(Conversation.contact),
        )
        .filter(Message.direction == MessageDirection.inbound)
        .filter(Message.channel_type.in_(selected_channels))
        .order_by(Message.created_at.desc())
        .limit(safe_limit)
        .all()
    )

    items: list[dict[str, Any]] = []
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    counts: Counter[str] = Counter()

    for message in messages:
        conversation = message.conversation
        person = conversation.contact if conversation else None
        provider = _provider_for_channel(message.channel_type)
        metadata = message.metadata_ if isinstance(message.metadata_, dict) else {}
        identity = _extract_sender_identity(message, person)
        display_name = preferred_meta_display_name(person, message.channel_type) if person else None
        if not display_name and person and isinstance(person.display_name, str):
            display_name = person.display_name.strip() or None
        message_attribution = _normalize_dict(metadata.get("attribution"))
        conversation_meta = conversation.metadata_ if conversation and isinstance(conversation.metadata_, dict) else {}
        conversation_attribution = _normalize_dict(conversation_meta.get("attribution"))
        raw_attribution = _extract_raw_attribution(metadata)
        classification = _classify_message(
            provider=provider,
            display_name=display_name,
            raw_attribution=raw_attribution,
            message_attribution=message_attribution,
            conversation_attribution=conversation_attribution,
        )
        item = {
            "provider": provider,
            "message_id": str(message.id),
            "conversation_id": str(message.conversation_id),
            "classification": classification,
            "timestamp": _serialize_timestamp(message.received_at or message.sent_at or message.created_at),
            "sender_id": identity.get("sender_id"),
            "sender_username": identity.get("sender_username"),
            "sender_name": identity.get("sender_name"),
            "display_name": display_name,
            "raw_attribution": raw_attribution,
            "message_attribution": message_attribution,
            "conversation_attribution": conversation_attribution,
            "has_attribution": bool(message_attribution or conversation_attribution),
            "raw_attribution_detected": bool(raw_attribution),
            "identity_placeholder": _is_meta_placeholder_name(display_name),
        }
        items.append(item)

        if classification == "meta_ads_attributed":
            groups["correctly_attributed_ads"].append(item)
        elif classification in {"instagram_organic", "facebook_organic", "whatsapp_organic"}:
            groups["correct_organic_identities"].append(item)
        elif classification == "missing_identity_fallback":
            groups["broken_fallback_identities"].append(item)
        elif classification == "attribution_lost_between_webhook_and_persistence":
            groups["missing_attribution_regression_cases"].append(item)

        counts["total_checked"] += 1
        if classification == "meta_ads_attributed":
            counts["ads_detected"] += 1
        if provider == "instagram" and classification == "missing_identity_fallback":
            counts["instagram_fallback_placeholders"] += 1
        if provider == "facebook" and classification == "missing_identity_fallback":
            counts["facebook_fallback_placeholders"] += 1
        if classification == "attribution_lost_between_webhook_and_persistence":
            counts["attribution_missing"] += 1

    return {
        "summary": {
            "total_checked": counts["total_checked"],
            "ads_detected": counts["ads_detected"],
            "instagram_fallback_placeholders": counts["instagram_fallback_placeholders"],
            "facebook_fallback_placeholders": counts["facebook_fallback_placeholders"],
            "attribution_missing": counts["attribution_missing"],
        },
        "providers": list(selected_providers),
        "groups": dict(groups),
        "items": items,
    }


def inspect_messenger_sender_diagnostics(
    db: Session,
    *,
    sender_id: str,
    page_id: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    clean_sender_id = sender_id.strip()
    clean_page_id = page_id.strip() if isinstance(page_id, str) and page_id.strip() else None
    channel = (
        db.query(PersonChannel)
        .options(joinedload(PersonChannel.person))
        .filter(PersonChannel.channel_type == PersonChannelType.facebook_messenger)
        .filter(PersonChannel.address == clean_sender_id)
        .order_by(PersonChannel.created_at.desc())
        .first()
    )
    person = channel.person if channel else None
    pending = None
    if channel and isinstance(channel.metadata_, dict):
        pending_raw = channel.metadata_.get("pending_meta_attribution")
        pending = dict(pending_raw) if isinstance(pending_raw, dict) else None
    conversations_query = None
    if person:
        conversations_query = (
            db.query(Conversation)
            .options(joinedload(Conversation.messages))
            .filter(Conversation.person_id == person.id)
            .order_by(Conversation.created_at.desc())
        )
    conversations = conversations_query.limit(limit).all() if conversations_query is not None else []

    conversation_items: list[dict[str, Any]] = []
    for conversation in conversations:
        conv_meta = conversation.metadata_ if isinstance(conversation.metadata_, dict) else {}
        messages: list[dict[str, Any]] = []
        ordered_messages = sorted(conversation.messages, key=lambda item: item.created_at or datetime.now(UTC))
        for message in ordered_messages[:limit]:
            msg_meta = message.metadata_ if isinstance(message.metadata_, dict) else {}
            messages.append(
                {
                    "message_id": str(message.id),
                    "direction": message.direction.value,
                    "body": message.body,
                    "timestamp": _serialize_timestamp(message.received_at or message.sent_at or message.created_at),
                    "message_attribution": _normalize_dict(msg_meta.get("attribution")),
                    "raw_attribution": _extract_raw_attribution(msg_meta),
                    "metadata_keys": sorted(msg_meta.keys()),
                }
            )
        conversation_items.append(
            {
                "conversation_id": str(conversation.id),
                "status": conversation.status.value,
                "created_at": _serialize_timestamp(conversation.created_at),
                "updated_at": _serialize_timestamp(conversation.updated_at),
                "conversation_attribution": _normalize_dict(conv_meta.get("attribution")),
                "messages": messages,
            }
        )

    suspected_ad_without_persistence = False
    if (
        clean_page_id
        and pending
        and pending.get("page_id") == clean_page_id
        and isinstance(pending.get("attribution"), dict)
    ):
        suspected_ad_without_persistence = True
    if (
        person
        and _is_meta_placeholder_name(person.display_name)
        and not any(conv.get("conversation_attribution") for conv in conversation_items)
        and any(message.get("raw_attribution") for conv in conversation_items for message in conv["messages"])
    ):
        suspected_ad_without_persistence = True

    return {
        "sender_id": clean_sender_id,
        "page_id": clean_page_id,
        "person": {
            "id": str(person.id) if person else None,
            "display_name": person.display_name if person else None,
            "metadata": dict(person.metadata_) if person and isinstance(person.metadata_, dict) else None,
            "placeholder_name": _is_meta_placeholder_name(person.display_name if person else None),
        },
        "channel": {
            "id": str(channel.id) if channel else None,
            "address": channel.address if channel else None,
            "metadata": dict(channel.metadata_) if channel and isinstance(channel.metadata_, dict) else None,
            "pending_meta_attribution": pending,
        },
        "conversations": conversation_items,
        "suspected_ad_without_persistence": suspected_ad_without_persistence,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect recent Meta inbound message attribution and identity state.")
    parser.add_argument("--limit", type=int, default=20, help="Number of recent inbound Meta messages to inspect.")
    parser.add_argument(
        "--providers",
        default="instagram,facebook,whatsapp",
        help="Comma-separated providers to inspect: instagram,facebook,whatsapp",
    )
    parser.add_argument("--sender-id", help="Inspect one Facebook Messenger sender/channel directly.")
    parser.add_argument("--page-id", help="Optional page id to compare against pending Messenger attribution.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output.")
    return parser.parse_args()


def _render_text(report: dict[str, Any]) -> str:
    lines = ["Summary:"]
    summary = report.get("summary", {})
    lines.extend(
        [
            f"  total_checked: {summary.get('total_checked', 0)}",
            f"  ads_detected: {summary.get('ads_detected', 0)}",
            f"  instagram_fallback_placeholders: {summary.get('instagram_fallback_placeholders', 0)}",
            f"  facebook_fallback_placeholders: {summary.get('facebook_fallback_placeholders', 0)}",
            f"  attribution_missing: {summary.get('attribution_missing', 0)}",
        ]
    )
    groups = report.get("groups", {})
    for group_name in (
        "correctly_attributed_ads",
        "correct_organic_identities",
        "broken_fallback_identities",
        "missing_attribution_regression_cases",
    ):
        lines.append(f"{group_name}:")
        items = groups.get(group_name, [])
        if not items:
            lines.append("  - none")
            continue
        for item in items:
            lines.append(
                "  - "
                f"{item['provider']} {item['message_id']} "
                f"classification={item['classification']} "
                f"display_name={item.get('display_name')!r} "
                f"sender_id={item.get('sender_id')!r}"
            )
    return "\n".join(lines)


def main() -> int:
    args = _parse_args()
    providers = [part for part in args.providers.split(",") if part.strip()]
    with SessionLocal() as db:
        if args.sender_id:
            report = inspect_messenger_sender_diagnostics(
                db,
                sender_id=args.sender_id,
                page_id=args.page_id,
                limit=args.limit,
            )
        else:
            report = get_recent_meta_message_attribution(db, limit=args.limit, providers=providers)
    if args.json:
        sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(_render_text(report) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
