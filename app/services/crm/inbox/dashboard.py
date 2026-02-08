"""Dashboard stats helpers for CRM inbox."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.services.crm.inbox.queries import get_inbox_stats, get_channel_stats


def load_inbox_stats(db: Session) -> tuple[dict, dict]:
    return get_inbox_stats(db), get_channel_stats(db)
