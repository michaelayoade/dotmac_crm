"""Compatibility wrapper for chat widget service."""

from app.services.crm.widget.service import (
    ChatWidgetConfigManager,
    WidgetVisitorManager,
    ChatWidgetConfigs,
    WidgetVisitorSessions,
    chat_widget_configs,
    widget_visitor_sessions,
    widget_configs,
    widget_visitors,
    receive_widget_message,
    send_widget_message,
    is_within_business_hours,
)

__all__ = [
    "ChatWidgetConfigManager",
    "WidgetVisitorManager",
    "ChatWidgetConfigs",
    "WidgetVisitorSessions",
    "chat_widget_configs",
    "widget_visitor_sessions",
    "widget_configs",
    "widget_visitors",
    "receive_widget_message",
    "send_widget_message",
    "is_within_business_hours",
]
