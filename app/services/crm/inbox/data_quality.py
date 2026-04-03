"""Daily data quality checks for CRM conversations."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation, ConversationTag
from app.models.crm.enums import ConversationStatus

logger = logging.getLogger(__name__)


def check_data_quality(db: Session, *, lookback_hours: int = 24) -> dict:
    """Check conversations resolved in the last N hours for missing data."""
    cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)

    resolved_base = db.query(Conversation).filter(
        Conversation.status == ConversationStatus.resolved,
        Conversation.is_active.is_(True),
        Conversation.resolved_at >= cutoff,
    )

    missing_first_response = resolved_base.filter(
        Conversation.first_response_at.is_(None),
    ).count()

    tagged_conv_ids = db.query(ConversationTag.conversation_id).distinct().subquery()
    missing_tags = resolved_base.filter(
        ~Conversation.id.in_(db.query(tagged_conv_ids.c.conversation_id)),
    ).count()

    return {
        "missing_first_response": missing_first_response,
        "missing_tags": missing_tags,
        "lookback_hours": lookback_hours,
    }


def run_data_quality_check_and_notify(db: Session) -> dict:
    """Run data quality check and create in-app notification for team leads."""
    from app.models.notification import Notification, NotificationChannel, NotificationStatus

    result = check_data_quality(db)
    total_issues = result["missing_first_response"] + result["missing_tags"]

    if total_issues == 0:
        logger.info("DATA_QUALITY_CHECK_COMPLETE no_issues=true")
        return result

    parts = []
    if result["missing_first_response"] > 0:
        parts.append(f"{result['missing_first_response']} missing first response")
    if result["missing_tags"] > 0:
        parts.append(f"{result['missing_tags']} missing tags")

    summary = ", ".join(parts)
    body = (
        f"Conversations resolved in the last 24 hours with data gaps: {summary}.\n"
        f"Open: /admin/crm/inbox?status=resolved&missing=first_response,tags"
    )

    db.add(
        Notification(
            channel=NotificationChannel.push,
            recipient="system:team_leads",
            subject=f"Data Quality: {total_issues} conversations with missing fields",
            body=body,
            status=NotificationStatus.delivered,
            sent_at=datetime.now(UTC),
        )
    )
    db.commit()

    return result
