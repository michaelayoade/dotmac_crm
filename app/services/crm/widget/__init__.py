"""CRM Widget submodule.

Handles chat widget configuration and visitor sessions.
"""

from app.services.crm.widget.service import (
    ChatWidgetConfigs,
    WidgetVisitorSessions,
    chat_widget_configs,
    widget_visitor_sessions,
)

__all__ = [
    "ChatWidgetConfigs",
    "WidgetVisitorSessions",
    "chat_widget_configs",
    "widget_visitor_sessions",
]
