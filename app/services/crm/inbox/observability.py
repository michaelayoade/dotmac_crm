"""Prometheus metrics for CRM inbox."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

INBOUND_MESSAGES = Counter(
    "inbox_inbound_messages_total",
    "Total inbound messages received",
    ["channel_type", "status"],  # status: success, duplicate, self_message, error
)

OUTBOUND_MESSAGES = Counter(
    "inbox_outbound_messages_total",
    "Total outbound messages sent",
    ["channel_type", "status"],  # status: sent, failed, retried
)

MESSAGE_PROCESSING_TIME = Histogram(
    "inbox_message_processing_seconds",
    "Time to process inbound/outbound messages",
    ["channel_type", "direction"],
)
