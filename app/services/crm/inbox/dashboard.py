"""Dashboard stats helpers for CRM inbox."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.services.crm.inbox.queries import get_channel_stats, get_inbox_stats, get_resolved_today_count


def load_inbox_stats(
    db: Session,
    *,
    timezone: str,
) -> tuple[dict, dict]:
    stats = get_inbox_stats(db)
    stats["resolved_today"] = get_resolved_today_count(db, timezone=timezone)
    return stats, get_channel_stats(db)
