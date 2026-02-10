"""Compatibility wrapper for chat widget service."""

from app.services.crm.widget.service import (
    ChatWidgetConfigManager,
    ChatWidgetConfigs,
    WidgetVisitorManager,
    WidgetVisitorSessions,
    chat_widget_configs,
    is_within_business_hours,
    receive_widget_message,
    send_widget_message,
    widget_configs,
    widget_visitor_sessions,
    widget_visitors,
)

__all__ = [
    "ChatWidgetConfigManager",
    "ChatWidgetConfigs",
    "WidgetVisitorManager",
    "WidgetVisitorSessions",
    "chat_widget_configs",
    "is_within_business_hours",
    "receive_widget_message",
    "send_widget_message",
    "widget_configs",
    "widget_visitor_sessions",
    "widget_visitors",
]
