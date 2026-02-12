"""WhatsApp template fetching for CRM inbox."""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.integration import IntegrationTarget, IntegrationTargetType
from app.services.common import coerce_uuid

_CACHE_TTL_SECONDS = 300
_TEMPLATE_CACHE: dict[str, tuple[float, list[dict]]] = {}


@dataclass(frozen=True)
class WhatsappTemplateConfig:
    base_url: str
    access_token: str
    business_account_id: str


def _resolve_whatsapp_config(db: Session, target_id: str | None) -> WhatsappTemplateConfig:
    target = None
    if target_id:
        target = db.get(IntegrationTarget, coerce_uuid(target_id))
    if not target:
        target = (
            db.query(IntegrationTarget)
            .join(ConnectorConfig, ConnectorConfig.id == IntegrationTarget.connector_config_id)
            .filter(IntegrationTarget.target_type == IntegrationTargetType.crm)
            .filter(IntegrationTarget.is_active.is_(True))
            .filter(ConnectorConfig.connector_type == ConnectorType.whatsapp)
            .order_by(IntegrationTarget.created_at.desc())
            .first()
        )
    if not target or not target.connector_config_id:
        raise HTTPException(status_code=400, detail="WhatsApp connector not configured")

    config = db.get(ConnectorConfig, target.connector_config_id)
    if not config or config.connector_type != ConnectorType.whatsapp:
        raise HTTPException(status_code=400, detail="WhatsApp connector not configured")

    auth_config = config.auth_config if isinstance(config.auth_config, dict) else {}
    access_token = auth_config.get("token") or auth_config.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="WhatsApp access token missing")

    metadata = config.metadata_ if isinstance(config.metadata_, dict) else {}
    business_account_id = metadata.get("business_account_id") or auth_config.get("business_account_id")
    if not business_account_id:
        raise HTTPException(status_code=400, detail="WhatsApp business account ID missing")

    base_url = config.base_url or "https://graph.facebook.com/v19.0"

    return WhatsappTemplateConfig(
        base_url=base_url.rstrip("/"),
        access_token=str(access_token),
        business_account_id=str(business_account_id),
    )


def _extract_body_text(components: list[dict]) -> str:
    for comp in components:
        if comp.get("type") == "BODY":
            return str(comp.get("text") or "")
    return ""


def list_whatsapp_templates(
    db: Session,
    *,
    target_id: str | None = None,
    limit: int = 200,
) -> list[dict]:
    cache_key = target_id or "default"
    cached = _TEMPLATE_CACHE.get(cache_key)
    if cached and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    config = _resolve_whatsapp_config(db, target_id)

    url = f"{config.base_url}/{config.business_account_id}/message_templates"
    headers = {"Authorization": f"Bearer {config.access_token}"}
    params = {"limit": max(1, min(limit, 200))}

    try:
        response = httpx.get(url, headers=headers, params=params, timeout=20.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail=f"WhatsApp templates fetch failed: {exc}") from exc

    payload = response.json() if response.content else {}
    data = payload.get("data") or []

    results: list[dict] = []
    for entry in data:
        components = entry.get("components") or []
        results.append(
            {
                "name": entry.get("name"),
                "language": entry.get("language"),
                "status": entry.get("status"),
                "category": entry.get("category"),
                "components": components,
                "body": _extract_body_text(components),
            }
        )

    _TEMPLATE_CACHE[cache_key] = (time.time(), results)
    return results
