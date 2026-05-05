#!/usr/bin/env python3
# ruff: noqa: T201, E402
"""Backfill Meta social conversation attribution and placeholder contact names.

Usage:
    poetry run python scripts/backfill_meta_social_identity.py --dry-run
    poetry run python scripts/backfill_meta_social_identity.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if "__file__" in globals():
    REPO_ROOT = Path(__file__).resolve().parent.parent
else:
    REPO_ROOT = Path.cwd()

sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(REPO_ROOT / ".env")

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.connector import ConnectorType
from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType
from app.models.oauth_token import OAuthToken
from app.models.person import ChannelType as PersonChannelType
from app.models.person import Person, PersonChannel
from app.services.meta_webhooks import (
    _fetch_profile_name,
    _get_facebook_access_token_override,
    _get_meta_graph_base_url,
    _persist_meta_attribution_to_conversation,
    _resolve_meta_connector,
    _split_display_name,
)
from app.services.person_identity import _is_meta_placeholder_name


def _conversation_backfill(db: Session, *, dry_run: bool) -> dict[str, int]:
    stats = {"conversations_scanned": 0, "conversations_updated": 0, "tags_added": 0}
    by_conversation: dict[str, tuple[Conversation, ChannelType, dict]] = {}

    rows = (
        db.query(Conversation, Message)
        .join(Message, Message.conversation_id == Conversation.id)
        .filter(Message.channel_type.in_([ChannelType.facebook_messenger, ChannelType.instagram_dm]))
        .order_by(Message.created_at.asc())
        .all()
    )

    for conversation, message in rows:
        metadata = message.metadata_ if isinstance(message.metadata_, dict) else {}
        attribution = metadata.get("attribution") if isinstance(metadata.get("attribution"), dict) else None
        if not attribution:
            continue
        conv_meta = conversation.metadata_ if isinstance(conversation.metadata_, dict) else {}
        existing_attr = conv_meta.get("attribution") if isinstance(conv_meta.get("attribution"), dict) else None
        if existing_attr:
            continue
        key = str(conversation.id)
        if key not in by_conversation:
            by_conversation[key] = (conversation, message.channel_type, attribution)

    stats["conversations_scanned"] = len(by_conversation)
    for conversation, channel_type, attribution in by_conversation.values():
        if dry_run:
            print(
                f"[DRY RUN] Conversation {conversation.id} would get Meta attribution "
                f"and tags from {channel_type.value}: {attribution}"
            )
            stats["conversations_updated"] += 1
            stats["tags_added"] += 2
            continue
        _persist_meta_attribution_to_conversation(
            db,
            conversation=conversation,
            channel=channel_type,
            attribution=attribution,
        )
        stats["conversations_updated"] += 1
        stats["tags_added"] += 2

    return stats


def _load_meta_tokens(db: Session) -> dict[str, str | None]:
    target, config = _resolve_meta_connector(db, ConnectorType.facebook)
    base_url = _get_meta_graph_base_url(db)
    page_tokens: dict[str, str] = {}
    ig_tokens: dict[str, str] = {}
    facebook_override_token = _get_facebook_access_token_override(db)

    if config:
        page_rows = (
            db.query(OAuthToken.external_account_id, OAuthToken.access_token)
            .filter(OAuthToken.connector_config_id == config.id)
            .filter(OAuthToken.provider == "meta")
            .filter(OAuthToken.account_type == "page")
            .filter(OAuthToken.is_active.is_(True))
            .all()
        )
        for account_id, access_token in page_rows:
            if account_id and access_token:
                page_tokens[str(account_id)] = access_token

        ig_rows = (
            db.query(OAuthToken.external_account_id, OAuthToken.access_token)
            .filter(OAuthToken.connector_config_id == config.id)
            .filter(OAuthToken.provider == "meta")
            .filter(OAuthToken.account_type == "instagram_business")
            .filter(OAuthToken.is_active.is_(True))
            .all()
        )
        for account_id, access_token in ig_rows:
            if account_id and access_token:
                ig_tokens[str(account_id)] = access_token

    return {
        "base_url": base_url,
        "facebook_override_token": facebook_override_token,
        "page_tokens": page_tokens,
        "ig_tokens": ig_tokens,
        "has_target": bool(target and config),
    }


def _best_message_context(db: Session, *, person_id, channel_type: PersonChannelType) -> tuple[str | None, str | None]:
    query = (
        db.query(Message)
        .join(PersonChannel, PersonChannel.id == Message.person_channel_id)
        .filter(PersonChannel.person_id == person_id)
        .filter(PersonChannel.channel_type == channel_type)
        .filter(Message.channel_type == ChannelType(channel_type.value))
        .order_by(Message.created_at.desc())
    )
    message = query.first()
    if not message:
        return None, None
    metadata = message.metadata_ if isinstance(message.metadata_, dict) else {}
    if channel_type == PersonChannelType.facebook_messenger:
        return str(metadata.get("page_id") or "").strip() or None, None
    if channel_type == PersonChannelType.instagram_dm:
        return str(metadata.get("instagram_account_id") or "").strip() or None, None
    return None, None


def _placeholder_name_backfill(db: Session, *, dry_run: bool) -> dict[str, int]:
    stats = {"people_scanned": 0, "people_updated": 0, "people_skipped": 0, "lookup_failures": 0}
    token_state = _load_meta_tokens(db)
    base_url = str(token_state["base_url"])
    page_tokens = dict(token_state["page_tokens"])  # type: ignore[arg-type]
    ig_tokens = dict(token_state["ig_tokens"])  # type: ignore[arg-type]
    facebook_override_token = token_state["facebook_override_token"]

    people = (
        db.query(Person)
        .filter(Person.is_active.is_(True))
        .filter(Person.display_name.isnot(None))
        .all()
    )

    for person in people:
        if not _is_meta_placeholder_name(person.display_name):
            continue
        stats["people_scanned"] += 1

        social_channels = [ch for ch in (person.channels or []) if ch.channel_type in {PersonChannelType.instagram_dm, PersonChannelType.facebook_messenger}]
        if not social_channels:
            stats["people_skipped"] += 1
            continue

        resolved_name: str | None = None
        for channel in social_channels:
            sender_id = str(channel.address or "").strip()
            if not sender_id:
                continue
            if channel.channel_type == PersonChannelType.instagram_dm:
                ig_account_id, _ = _best_message_context(db, person_id=person.id, channel_type=channel.channel_type)
                if not ig_account_id:
                    continue
                access_token = ig_tokens.get(ig_account_id)
                if not access_token:
                    continue
                resolved_name = _fetch_profile_name(access_token, sender_id, "username,name", base_url)
            else:
                page_id, _ = _best_message_context(db, person_id=person.id, channel_type=channel.channel_type)
                access_token = facebook_override_token or (page_tokens.get(page_id) if page_id else None)
                if not access_token:
                    continue
                resolved_name = _fetch_profile_name(access_token, sender_id, "name", base_url)

            if resolved_name and not _is_meta_placeholder_name(resolved_name):
                break

        if not resolved_name or _is_meta_placeholder_name(resolved_name):
            stats["lookup_failures"] += 1
            continue

        first_name, last_name, display_name = _split_display_name(resolved_name)
        if dry_run:
            print(
                f"[DRY RUN] Person {person.id} would change name "
                f"from '{person.display_name}' to '{display_name}'"
            )
            stats["people_updated"] += 1
            continue

        person.display_name = display_name
        if person.first_name == "Unknown":
            person.first_name = first_name[:80]
        if person.last_name == "Unknown":
            person.last_name = last_name[:80]
        stats["people_updated"] += 1

    return stats


def backfill(*, dry_run: bool) -> dict[str, dict[str, int]]:
    db = SessionLocal()
    try:
        conversation_stats = _conversation_backfill(db, dry_run=dry_run)
        people_stats = _placeholder_name_backfill(db, dry_run=dry_run)
        if dry_run:
            db.rollback()
            print("[DRY RUN] No changes committed.")
        else:
            db.commit()
            print("Committed Meta social identity backfill.")
        return {
            "conversations": conversation_stats,
            "people": people_stats,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Meta social conversation attribution and placeholder names.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without committing")
    args = parser.parse_args()

    result = backfill(dry_run=args.dry_run)
    print(result)


if __name__ == "__main__":
    main()
