"""Compatibility wrapper for inbox deduplication helpers."""

from app.services.crm.inbox.dedup import (
    _build_inbound_dedupe_id,
    _find_duplicate_inbound_message,
)

__all__ = [
    "_build_inbound_dedupe_id",
    "_find_duplicate_inbound_message",
]
