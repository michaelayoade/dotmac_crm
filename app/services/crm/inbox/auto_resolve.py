"""Auto-resolve idle conversations for CRM inbox."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation
from app.models.crm.enums import ConversationStatus
from app.models.domain_settings import SettingDomain
from app.services.crm.inbox import cache as inbox_cache
from app.services.settings_spec import resolve_value

logger = logging.getLogger(__name__)

MAX_BATCH_SIZE = 500


def auto_resolve_idle_conversations(db: Session) -> dict:
    """Resolve open/pending conversations that have been idle for N days.

    Reads configuration from domain settings:
      - crm_inbox_auto_resolve_enabled (bool)
      - crm_inbox_auto_resolve_days (int)

    Returns dict with counts for monitoring.
    """
    enabled = resolve_value(db, SettingDomain.notification, "crm_inbox_auto_resolve_enabled")
    if not enabled:
        return {"skipped": True, "reason": "disabled"}

    days = resolve_value(db, SettingDomain.notification, "crm_inbox_auto_resolve_days")
    if not isinstance(days, int) or days < 1:
        days = 7

    now = datetime.now(UTC)
    threshold = now - timedelta(days=days)

    idle_conversations = (
        db.query(Conversation)
        .filter(Conversation.is_active.is_(True))
        .filter(Conversation.status.in_([ConversationStatus.open, ConversationStatus.pending]))
        .filter(Conversation.last_message_at <= threshold)
        .limit(MAX_BATCH_SIZE)
        .all()
    )

    resolved_count = 0
    errors: list[str] = []

    for conv in idle_conversations:
        conv.status = ConversationStatus.resolved
        resolved_count += 1

    if resolved_count > 0:
        try:
            db.commit()
            inbox_cache.invalidate_inbox_list()
        except Exception as exc:
            db.rollback()
            logger.exception("AUTO_RESOLVE_COMMIT_FAILED count=%d", resolved_count)
            errors.append(f"commit: {exc}")
            resolved_count = 0

    logger.info(
        "AUTO_RESOLVE_COMPLETE resolved=%d errors=%d threshold_days=%d",
        resolved_count,
        len(errors),
        days,
    )

    return {
        "resolved": resolved_count,
        "errors": errors,
        "threshold_days": days,
    }
