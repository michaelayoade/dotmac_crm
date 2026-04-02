"""One-time backfill for conversation metric fields.

Populates first_response_at and resolved_at from existing message and status data.

Usage:
    poetry run python scripts/backfill_conversation_metrics.py [--dry-run]
"""

import sys
from datetime import UTC, datetime

from app.db import SessionLocal
from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ConversationStatus, MessageDirection
from app.models.crm.team import CrmAgent


def backfill(dry_run: bool = False) -> dict:
    db = SessionLocal()
    stats = {"first_response_filled": 0, "resolved_at_filled": 0, "errors": []}

    try:
        # 1. Backfill first_response_at
        convs_missing_frt = (
            db.query(Conversation)
            .filter(Conversation.first_response_at.is_(None), Conversation.is_active.is_(True))
            .all()
        )

        agent_person_ids = set(
            row[0] for row in db.query(CrmAgent.person_id).filter(CrmAgent.is_active.is_(True)).all()
        )

        for conv in convs_missing_frt:
            first_agent_msg = (
                db.query(Message)
                .filter(
                    Message.conversation_id == conv.id,
                    Message.direction == MessageDirection.outbound,
                    Message.author_id.in_(agent_person_ids),
                )
                .order_by(Message.created_at.asc())
                .first()
            )
            if first_agent_msg:
                timestamp = first_agent_msg.sent_at or first_agent_msg.created_at
                conv.first_response_at = timestamp
                conv.response_time_seconds = int((timestamp - conv.created_at).total_seconds())
                stats["first_response_filled"] += 1

        # 2. Backfill resolved_at
        convs_missing_resolved = (
            db.query(Conversation)
            .filter(
                Conversation.status == ConversationStatus.resolved,
                Conversation.resolved_at.is_(None),
                Conversation.is_active.is_(True),
            )
            .all()
        )

        for conv in convs_missing_resolved:
            conv.resolved_at = conv.updated_at
            conv.resolution_time_seconds = int((conv.updated_at - conv.created_at).total_seconds())
            stats["resolved_at_filled"] += 1

        if dry_run:
            print(f"DRY RUN — would fill {stats['first_response_filled']} first_response_at, "
                  f"{stats['resolved_at_filled']} resolved_at")
            db.rollback()
        else:
            db.commit()
            print(f"Backfilled {stats['first_response_filled']} first_response_at, "
                  f"{stats['resolved_at_filled']} resolved_at")

    except Exception as exc:
        db.rollback()
        stats["errors"].append(str(exc))
        print(f"ERROR: {exc}")
        raise
    finally:
        db.close()

    return stats


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    backfill(dry_run=dry_run)
