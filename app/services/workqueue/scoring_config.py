"""Tunable thresholds + ordering for Workqueue scoring."""

from __future__ import annotations

from app.services.workqueue.types import ItemKind

CONVERSATION_SCORES: dict[str, int] = {
    "sla_breach": 100,
    "sla_imminent": 90,
    "sla_soon": 75,
    "mention": 65,
    "awaiting_reply_long": 55,
    "assigned_unread": 45,
}

TICKET_SCORES: dict[str, int] = {
    "sla_breach": 100,
    "sla_imminent": 90,
    "priority_urgent": 80,
    "sla_soon": 75,
    "overdue": 70,
    "customer_replied": 65,
}

LEAD_QUOTE_SCORES: dict[str, int] = {
    "quote_expires_today": 85,
    "lead_overdue_followup": 70,
    "quote_expires_3d": 65,
    "lead_high_value_idle_3d": 60,
    "quote_sent_no_response_7d": 50,
}

TASK_SCORES: dict[str, int] = {
    "overdue": 80,
    "due_today": 70,
    "blocked_dependency_resolved": 60,
    "assigned_recently_unread": 40,
}

# Stable section/tie-break ordering
KIND_ORDER: dict[ItemKind, int] = {
    ItemKind.conversation: 0,
    ItemKind.ticket: 1,
    ItemKind.lead: 2,
    ItemKind.quote: 3,
    ItemKind.task: 4,
}

# UI section ordering
SECTION_ORDER: tuple[ItemKind, ...] = (
    ItemKind.conversation,
    ItemKind.ticket,
    ItemKind.lead,
    ItemKind.quote,
    ItemKind.task,
)

# SLA windows (seconds)
CONV_SLA_IMMINENT_SEC = 5 * 60
CONV_SLA_SOON_SEC = 30 * 60
TICKET_SLA_IMMINENT_SEC = 15 * 60
TICKET_SLA_SOON_SEC = 2 * 3600

# Default per-provider fetch limit
PROVIDER_LIMIT = 50
DEFAULT_HERO_BAND_SIZE = 6
