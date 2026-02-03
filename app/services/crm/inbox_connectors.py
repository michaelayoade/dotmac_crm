"""Compatibility wrapper for inbox connector helpers."""

from app.services.crm.inbox.connectors import (
    _get_whatsapp_api_timeout,
    _resolve_connector_config,
    _resolve_integration_target,
    _smtp_config_from_connector,
)

__all__ = [
    "_get_whatsapp_api_timeout",
    "_resolve_connector_config",
    "_resolve_integration_target",
    "_smtp_config_from_connector",
]
