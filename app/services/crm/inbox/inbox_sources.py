"""CRM inbox source helpers for API and live updates."""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.integration import IntegrationTarget, IntegrationTargetType

_CONNECTOR_LABELS: dict[ConnectorType, str] = {
    ConnectorType.email: "Email Inbox",
    ConnectorType.whatsapp: "WhatsApp Inbox",
    ConnectorType.facebook: "Facebook Inbox",
    ConnectorType.instagram: "Instagram Inbox",
}


def _channel_type_label(connector_type: ConnectorType | None) -> str:
    if connector_type in _CONNECTOR_LABELS:
        return _CONNECTOR_LABELS[connector_type]
    raw = getattr(connector_type, "value", "") or ""
    return f"{raw.replace('_', ' ').title()} Inbox".strip()


def serialize_inbox_source(
    target: IntegrationTarget,
    connector_type: ConnectorType | None,
) -> dict[str, str]:
    channel_type = _channel_type_label(connector_type)
    name = (target.name or "").strip() or "Inbox"
    return {
        "id": str(target.id),
        "name": name,
        "channel_type": channel_type,
        "display_label": f"{channel_type} · {name}",
    }


def list_inbox_sources(db: Session) -> list[dict[str, str]]:
    rows = (
        db.query(IntegrationTarget, ConnectorConfig.connector_type)
        .join(ConnectorConfig, ConnectorConfig.id == IntegrationTarget.connector_config_id)
        .filter(IntegrationTarget.target_type == IntegrationTargetType.crm)
        .filter(IntegrationTarget.is_active.is_(True))
        .filter(ConnectorConfig.is_active.is_(True))
        .order_by(
            ConnectorConfig.connector_type.asc(),
            func.lower(IntegrationTarget.name).asc(),
            IntegrationTarget.created_at.asc(),
        )
        .all()
    )
    return [serialize_inbox_source(target, connector_type) for target, connector_type in rows]
