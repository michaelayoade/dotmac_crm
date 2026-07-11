"""Receiver for the Sub → CRM vendor project-stub relay (Phase 3, risk #6).

dotmac_sub owns project data natively after the Phase 3 write-flip, but CRM's
vendor wrapper still FKs CRM ``projects``: ``installation_projects.project_id``
(NOT NULL + unique), plus ``wireless_surveys`` / ``material_requests`` /
``expense_requests``. This upserts a minimal stub row so those FKs keep resolving
until the Phase 5 vendor port, when the whole relay is deleted.

Invariants:
* **Idempotent** on the project id (== the sub project UUID, doc 20 §3.4
  shared-UUID strategy) — sub can re-push on every project update.
* **No-clobber**: a CRM-native project row (one predating the flip, or any row
  whose ``metadata.source`` is not ``sub_relay``) is never overwritten — only
  the stub fields of relay-owned rows are touched.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.logging import get_logger
from app.models.projects import Project, ProjectStatus, ProjectType

logger = get_logger(__name__)

RELAY_SOURCE = "sub_relay"


class RelayPayloadError(ValueError):
    """Raised when a relay payload is missing required stub fields."""


def _coerce_status(value: Any) -> ProjectStatus:
    try:
        return ProjectStatus(str(value))
    except (ValueError, TypeError):
        return ProjectStatus.open


def _coerce_type(value: Any) -> ProjectType | None:
    if value in (None, ""):
        return None
    try:
        return ProjectType(str(value))
    except (ValueError, TypeError):
        return None


def upsert_project_stub(db, payload: dict[str, Any]) -> dict[str, str]:
    """Idempotently upsert a relayed project stub. Returns an action dict.

    Raises ``RelayPayloadError`` (→ 400) on a missing id/name.
    """
    raw_id = payload.get("id")
    if not raw_id:
        raise RelayPayloadError("Relay payload missing project id")
    try:
        project_id = uuid.UUID(str(raw_id))
    except (ValueError, TypeError) as exc:
        raise RelayPayloadError("Relay payload has an invalid project id") from exc

    name = payload.get("name")
    if not name or not str(name).strip():
        raise RelayPayloadError("Relay payload missing project name")

    status = _coerce_status(payload.get("status"))
    project_type = _coerce_type(payload.get("project_type"))
    customer_address = payload.get("customer_address")
    region = payload.get("region")
    subscriber_external_ref = payload.get("subscriber_external_ref")

    existing = db.get(Project, project_id)
    if existing is not None:
        meta = existing.metadata_ or {}
        if meta.get("source") != RELAY_SOURCE:
            # Native CRM row — never clobber (guards rows that predate the flip).
            logger.info("project_relay_skip_native project_id=%s", project_id)
            return {"action": "skipped_native", "project_id": str(project_id)}
        # Relay-owned stub → refresh its stub fields only. Templates, tasks,
        # people, service_team, erpnext_id … are never touched by the relay.
        existing.name = str(name)
        existing.status = status
        existing.project_type = project_type
        existing.customer_address = customer_address
        existing.region = region
        existing.is_active = True
        existing.metadata_ = _stub_metadata(meta, subscriber_external_ref)
        db.commit()
        return {"action": "updated", "project_id": str(project_id)}

    project = Project(
        id=project_id,
        name=str(name),
        status=status,
        project_type=project_type,
        customer_address=customer_address,
        region=region,
        metadata_=_stub_metadata({}, subscriber_external_ref),
        is_active=True,
    )
    db.add(project)
    db.commit()
    logger.info("project_relay_created project_id=%s", project_id)
    return {"action": "created", "project_id": str(project_id)}


def _stub_metadata(existing: dict, subscriber_external_ref: Any) -> dict:
    meta = dict(existing or {})
    meta["source"] = RELAY_SOURCE
    meta["relayed_at"] = datetime.now(UTC).isoformat()
    if subscriber_external_ref:
        # Provenance only — the sub subscriber ref (== CRM subscriber UUID). Not
        # written to the FK: the vendor flow sets installation_projects.subscriber_id
        # itself; a stub never fabricates a projects.subscriber_id FK value.
        meta["sub_subscriber_ref"] = str(subscriber_external_ref)
    return meta
