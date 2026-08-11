"""Dashboard stats helpers for CRM inbox."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.services.crm.inbox import cache as inbox_cache
from app.services.crm.inbox.queries import get_channel_stats, get_inbox_stats, get_resolved_today_count


def load_inbox_stats(
    db: Session,
    *,
    timezone: str,
) -> tuple[dict, dict]:
    stats = dict(
        inbox_cache.get_or_set(
            inbox_cache.build_summary_counts_key({"kind": "inbox_stats"}),
            inbox_cache.SUMMARY_COUNTS_TTL_SECONDS,
            lambda: get_inbox_stats(db),
        )
    )
    stats["resolved_today"] = inbox_cache.get_or_set(
        inbox_cache.build_summary_counts_key(
            {"kind": "resolved_today", "timezone": timezone}
        ),
        inbox_cache.SUMMARY_COUNTS_TTL_SECONDS,
        lambda: get_resolved_today_count(db, timezone=timezone),
    )
    channel_stats = dict(
        inbox_cache.get_or_set(
            inbox_cache.build_summary_counts_key({"kind": "channel_stats"}),
            inbox_cache.SUMMARY_COUNTS_TTL_SECONDS,
            lambda: get_channel_stats(db),
        )
    )
    return stats, channel_stats
