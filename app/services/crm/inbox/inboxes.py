"""Inbox channel helpers for CRM inbox."""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.models.integration import (
    ConnectorConfig,
    ConnectorType,
    IntegrationJob,
    IntegrationJobType,
    IntegrationTarget,
    IntegrationTargetType,
)
from app.services import integration as integration_service

logger = logging.getLogger(__name__)


def _safe_log_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=True, default=str, sort_keys=True)


def get_email_channel_state(db: Session) -> dict | None:
    state = integration_service.integration_targets.get_channel_state(
        db, IntegrationTargetType.crm, ConnectorType.email
    )
    if state:
        smtp = state.get("smtp")
        imap = state.get("imap")
        pop3 = state.get("pop3")
        logger.info(
            "crm_inbox_email_state %s",
            _safe_log_json({
                "target_id": state.get("target_id"),
                "connector_id": state.get("connector_id"),
                "smtp": {
                    "host": smtp.get("host") if isinstance(smtp, dict) else None,
                    "port": smtp.get("port") if isinstance(smtp, dict) else None,
                },
                "imap": {
                    "host": imap.get("host") if isinstance(imap, dict) else None,
                    "port": imap.get("port") if isinstance(imap, dict) else None,
                },
                "pop3": {
                    "host": pop3.get("host") if isinstance(pop3, dict) else None,
                    "port": pop3.get("port") if isinstance(pop3, dict) else None,
                },
                "poll_interval_seconds": state.get("poll_interval_seconds"),
            }),
        )
    return state


def get_whatsapp_channel_state(db: Session) -> dict | None:
    return integration_service.integration_targets.get_channel_state(
        db, IntegrationTargetType.crm, ConnectorType.whatsapp
    )


def build_email_state_for_target(
    db: Session,
    target: IntegrationTarget,
    config: ConnectorConfig,
) -> dict:
    metadata = config.metadata_ if isinstance(config.metadata_, dict) else {}
    auth_config = config.auth_config if isinstance(config.auth_config, dict) else {}
    job = (
        db.query(IntegrationJob)
        .filter(IntegrationJob.target_id == target.id)
        .filter(IntegrationJob.job_type == IntegrationJobType.import_)
        .order_by(IntegrationJob.created_at.desc())
        .first()
    )
    poll_interval = None
    if job:
        if job.interval_seconds is not None:
            poll_interval = job.interval_seconds
        elif job.interval_minutes:
            poll_interval = job.interval_minutes * 60
    return {
        "target_id": str(target.id),
        "connector_id": str(config.id),
        "name": target.name or config.name,
        "auth_config": auth_config,
        "smtp": metadata.get("smtp"),
        "imap": metadata.get("imap"),
        "pop3": metadata.get("pop3"),
        "poll_interval_seconds": poll_interval,
        "polling_active": bool(job and job.is_active),
        "receiving_enabled": bool((metadata.get("imap") or metadata.get("pop3")) and job and job.is_active),
        "is_active": bool(target.is_active),
        "connector_active": bool(config.is_active),
    }


def build_whatsapp_state_for_target(
    target: IntegrationTarget,
    config: ConnectorConfig,
) -> dict:
    metadata = config.metadata_ if isinstance(config.metadata_, dict) else {}
    auth_config = config.auth_config if isinstance(config.auth_config, dict) else {}
    return {
        "target_id": str(target.id),
        "connector_id": str(config.id),
        "name": target.name or config.name,
        "auth_config": auth_config,
        "base_url": config.base_url,
        "phone_number_id": metadata.get("phone_number_id"),
        "is_active": bool(target.is_active),
        "connector_active": bool(config.is_active),
    }


def list_channel_targets(db: Session, connector_type: ConnectorType) -> list[dict]:
    targets = (
        db.query(IntegrationTarget)
        .join(ConnectorConfig, ConnectorConfig.id == IntegrationTarget.connector_config_id)
        .filter(IntegrationTarget.target_type == IntegrationTargetType.crm)
        .filter(ConnectorConfig.connector_type == connector_type)
        .order_by(IntegrationTarget.created_at.desc())
        .all()
    )
    results = []
    for target in targets:
        config = target.connector_config
        if not config:
            continue
        if connector_type == ConnectorType.email:
            payload = build_email_state_for_target(db, target, config)
            payload["channel"] = connector_type.value
            payload["kind"] = "inbox"
            results.append(payload)
        elif connector_type == ConnectorType.whatsapp:
            payload = build_whatsapp_state_for_target(target, config)
            payload["channel"] = connector_type.value
            payload["kind"] = "inbox"
            results.append(payload)
        else:
            name = target.name or config.name
            results.append(
                {
                    "target_id": str(target.id),
                    "connector_id": str(config.id),
                    "name": name,
                    "channel": connector_type.value,
                    "kind": "inbox",
                }
            )
    return results
