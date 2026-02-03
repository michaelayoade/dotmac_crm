#!/usr/bin/env python3
"""Fix Chatwoot imported data - corrects channel types and message directions."""

import logging
import sys
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from app.db import SessionLocal
from app.services.chatwoot.client import ChatwootClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# Channel type mapping from Chatwoot channel names
CHANNEL_MAP = {
    "Channel::WebWidget": "chat_widget",
    "Channel::Email": "email",
    "Channel::Whatsapp": "whatsapp",
    "Channel::FacebookPage": "facebook_messenger",
    "Channel::Instagram": "instagram_dm",
    "Channel::TwitterProfile": "chat_widget",
    "Channel::Api": "chat_widget",
    "Channel::Sms": "chat_widget",
    "Channel::TwilioSms": "chat_widget",
    "Channel::Line": "chat_widget",
    "Channel::Telegram": "chat_widget",
}


def get_chatwoot_settings(db):
    """Get chatwoot settings from domain_settings."""
    result = db.execute(text("""
        SELECT key, value_text FROM domain_settings
        WHERE key IN ('chatwoot_base_url', 'chatwoot_access_token', 'chatwoot_account_id')
    """)).fetchall()

    settings = {row[0]: row[1] for row in result}
    return settings


def fix_conversation_channels_from_api(db, client):
    """Re-fetch conversation metadata from Chatwoot API to get correct channel."""
    logger.info("Fetching conversation channels from Chatwoot API...")

    # Get all chatwoot conversation IDs we need to update
    result = db.execute(text("""
        SELECT id, metadata->>'chatwoot_id' as cw_id
        FROM crm_conversations
        WHERE metadata::text LIKE '%chatwoot%'
        AND (metadata->>'chatwoot_channel' IS NULL OR metadata->>'chatwoot_channel' = '')
    """)).fetchall()

    logger.info(f"Found {len(result)} conversations to update")

    updated = 0
    errors = 0
    batch_size = 100

    for i, row in enumerate(result):
        local_id, cw_id = row

        if not cw_id:
            continue

        try:
            # Fetch conversation detail from chatwoot
            detail = client.get_conversation(int(cw_id))
            meta = detail.get("meta", {})
            channel = meta.get("channel")
            team = meta.get("team", {})
            inbox_id = detail.get("inbox_id")

            if channel:
                local_channel = CHANNEL_MAP.get(channel, "chat_widget")

                db.execute(text("""
                    UPDATE crm_conversations
                    SET metadata = jsonb_set(
                        jsonb_set(
                            jsonb_set(
                                metadata::jsonb,
                                '{chatwoot_channel}',
                                :channel::jsonb
                            ),
                            '{chatwoot_inbox_id}',
                            :inbox_id::jsonb
                        ),
                        '{channel_type}',
                        :local_channel::jsonb
                    )
                    WHERE id = :local_id
                """), {
                    "channel": f'"{channel}"',
                    "inbox_id": str(inbox_id) if inbox_id else "null",
                    "local_channel": f'"{local_channel}"',
                    "local_id": str(local_id),
                })
                updated += 1

        except Exception as e:
            errors += 1
            if errors <= 10:
                logger.warning(f"Error fetching conversation {cw_id}: {e}")

        # Commit in batches
        if (i + 1) % batch_size == 0:
            db.commit()
            logger.info(f"  Processed {i + 1}/{len(result)} conversations ({updated} updated, {errors} errors)")

    db.commit()
    logger.info(f"Completed: {updated} updated, {errors} errors")


def fix_message_channels(db):
    """Update message channel_type from conversation metadata."""
    logger.info("Fixing message channel types...")

    # Update messages to match their conversation's channel type
    updated = db.execute(text("""
        UPDATE crm_messages m
        SET channel_type = (
            SELECT
                CASE c.metadata->>'channel_type'
                    WHEN 'whatsapp' THEN 'whatsapp'
                    WHEN 'email' THEN 'email'
                    WHEN 'facebook_messenger' THEN 'facebook_messenger'
                    WHEN 'instagram_dm' THEN 'instagram_dm'
                    ELSE 'chat_widget'
                END
            FROM crm_conversations c
            WHERE c.id = m.conversation_id
        )
        WHERE m.metadata::text LIKE '%chatwoot%'
        AND EXISTS (
            SELECT 1 FROM crm_conversations c
            WHERE c.id = m.conversation_id
            AND c.metadata->>'channel_type' IS NOT NULL
            AND c.metadata->>'channel_type' != ''
        )
    """))

    logger.info(f"  Updated {updated.rowcount} message channel types")
    db.commit()


def fix_message_directions(db):
    """Fix message directions based on chatwoot_message_type."""
    logger.info("Fixing message directions...")

    # Count current state
    result = db.execute(text("""
        SELECT
            metadata->>'chatwoot_message_type' as msg_type,
            direction,
            COUNT(*) as count
        FROM crm_messages
        WHERE metadata::text LIKE '%chatwoot%'
        GROUP BY metadata->>'chatwoot_message_type', direction
        ORDER BY count DESC
    """)).fetchall()

    logger.info("Current message type/direction distribution:")
    for row in result:
        logger.info(f"  Type {row[0]}, Direction {row[1]}: {row[2]} messages")

    # Fix type 1 -> outbound (agent messages)
    updated = db.execute(text("""
        UPDATE crm_messages
        SET direction = 'outbound',
            status = 'sent'
        WHERE metadata::text LIKE '%chatwoot%'
        AND (metadata->>'chatwoot_message_type')::int = 1
        AND direction != 'outbound'
    """))
    logger.info(f"  Fixed {updated.rowcount} type 1 messages to outbound")

    # Fix type 2 -> internal (activity/system messages)
    updated = db.execute(text("""
        UPDATE crm_messages
        SET direction = 'internal'
        WHERE metadata::text LIKE '%chatwoot%'
        AND (metadata->>'chatwoot_message_type')::int = 2
        AND direction != 'internal'
    """))
    logger.info(f"  Fixed {updated.rowcount} type 2 messages to internal")

    # Fix type 3 -> outbound (template messages)
    updated = db.execute(text("""
        UPDATE crm_messages
        SET direction = 'outbound',
            status = 'sent'
        WHERE metadata::text LIKE '%chatwoot%'
        AND (metadata->>'chatwoot_message_type')::int = 3
        AND direction != 'outbound'
    """))
    logger.info(f"  Fixed {updated.rowcount} type 3 messages to outbound")

    db.commit()


def show_final_stats(db):
    """Show final statistics after fixes."""
    logger.info("\n=== Final Statistics ===")

    # Channel distribution for conversations
    result = db.execute(text("""
        SELECT metadata->>'channel_type' as channel, COUNT(*) as count
        FROM crm_conversations
        WHERE metadata::text LIKE '%chatwoot%'
        GROUP BY metadata->>'channel_type'
        ORDER BY count DESC
    """)).fetchall()

    logger.info("Conversations by channel type:")
    for row in result:
        logger.info(f"  {row[0] or 'NULL'}: {row[1]}")

    # Channel distribution for messages
    result = db.execute(text("""
        SELECT channel_type, COUNT(*) as count
        FROM crm_messages
        WHERE metadata::text LIKE '%chatwoot%'
        GROUP BY channel_type
        ORDER BY count DESC
    """)).fetchall()

    logger.info("Messages by channel type:")
    for row in result:
        logger.info(f"  {row[0]}: {row[1]}")

    # Direction distribution
    result = db.execute(text("""
        SELECT direction, COUNT(*) as count
        FROM crm_messages
        WHERE metadata::text LIKE '%chatwoot%'
        GROUP BY direction
        ORDER BY count DESC
    """)).fetchall()

    logger.info("Messages by direction:")
    for row in result:
        logger.info(f"  {row[0]}: {row[1]}")


def main():
    logger.info("Starting Chatwoot data fix...")

    db = SessionLocal()
    client = None

    try:
        # Get chatwoot settings
        settings = get_chatwoot_settings(db)
        base_url = settings.get("chatwoot_base_url")
        token = settings.get("chatwoot_access_token")
        account_id = int(settings.get("chatwoot_account_id", 1))

        if not base_url or not token:
            logger.error("Chatwoot settings not found in domain_settings")
            sys.exit(1)

        logger.info(f"Connecting to Chatwoot at {base_url}")
        client = ChatwootClient(base_url=base_url, access_token=token, account_id=account_id)

        # Test connection
        if not client.test_connection():
            logger.error("Failed to connect to Chatwoot API")
            sys.exit(1)

        # Fix data
        fix_conversation_channels_from_api(db, client)
        fix_message_channels(db)
        fix_message_directions(db)
        show_final_stats(db)

        logger.info("\nChatwoot data fix complete!")

    except Exception as e:
        logger.exception(f"Error during fix: {e}")
        db.rollback()
        sys.exit(1)
    finally:
        if client:
            client.close()
        db.close()


if __name__ == "__main__":
    main()
