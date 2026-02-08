"""Target resolution helpers for CRM inbox."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.integration import IntegrationTarget, IntegrationTargetType
from app.services.common import coerce_uuid


def resolve_target_and_config(
    db: Session,
    target_id: str | None,
    connector_id: str | None,
    connector_type: ConnectorType,
) -> tuple[IntegrationTarget | None, ConnectorConfig | None]:
    target = None
    if target_id:
        target = db.get(IntegrationTarget, coerce_uuid(target_id))
    elif connector_id:
        target = (
            db.query(IntegrationTarget)
            .filter(IntegrationTarget.connector_config_id == coerce_uuid(connector_id))
            .filter(IntegrationTarget.target_type == IntegrationTargetType.crm)
            .order_by(IntegrationTarget.created_at.desc())
            .first()
        )
    if not target:
        return None, None
    if target.target_type != IntegrationTargetType.crm:
        raise ValueError("Target must be crm type")
    if not target.connector_config_id:
        raise ValueError("Target missing connector config")
    config = db.get(ConnectorConfig, target.connector_config_id)
    if not config:
        raise ValueError("Connector config not found")
    if config.connector_type != connector_type:
        raise ValueError("Connector type mismatch")
    return target, config
