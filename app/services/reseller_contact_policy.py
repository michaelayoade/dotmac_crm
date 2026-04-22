from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models.subscriber import AccountType, Organization

RESELLER_PLACEHOLDER_DOMAIN = "reseller.dotmac.ng"
RESELLER_CONTACT_METADATA_KEY = "reseller_linked_contact"
COMM_OWNER_ORG_ID_METADATA_KEY = "comm_owner_org_id"
CONTACT_IDENTITY_MODE_METADATA_KEY = "contact_identity_mode"
CONTACT_IDENTITY_MODE_RESELLER_PROXY = "reseller_proxy"


def _coerce_uuid(value: uuid.UUID | str | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    candidate = str(value).strip()
    if not candidate:
        return None
    try:
        return uuid.UUID(candidate)
    except ValueError:
        return None


def resolve_reseller_owner_org_id(
    db: Session,
    organization_id: uuid.UUID | str | None,
) -> uuid.UUID | None:
    """Return nearest reseller ancestor (or self) for an organization."""
    current_id = _coerce_uuid(organization_id)
    if not current_id:
        return None

    visited: set[uuid.UUID] = set()
    while current_id and current_id not in visited:
        visited.add(current_id)
        org = db.get(Organization, current_id)
        if not org:
            return None
        if org.account_type == AccountType.reseller:
            return org.id
        current_id = org.parent_id
    return None


def is_reseller_linked_org(db: Session, organization_id: uuid.UUID | str | None) -> bool:
    return resolve_reseller_owner_org_id(db, organization_id) is not None


def build_reseller_placeholder_email(
    organization_id: uuid.UUID | str,
    token: str | None = None,
) -> str:
    org_part = str(_coerce_uuid(organization_id) or organization_id).lower()
    suffix = (token or uuid.uuid4().hex).strip().lower()
    if not suffix:
        suffix = uuid.uuid4().hex
    return f"{org_part}-{suffix}@{RESELLER_PLACEHOLDER_DOMAIN}"


def resolve_reseller_placeholder_email(
    current_email: str | None,
    organization_id: uuid.UUID | str,
) -> str:
    org_part = str(_coerce_uuid(organization_id) or organization_id).lower()
    existing = (current_email or "").strip().lower()
    if existing.endswith(f"@{RESELLER_PLACEHOLDER_DOMAIN}") and existing.startswith(f"{org_part}-"):
        return existing
    return build_reseller_placeholder_email(organization_id)


def with_reseller_contact_metadata(
    metadata: dict | None,
    *,
    reseller_owner_org_id: uuid.UUID | str | None,
) -> dict | None:
    payload = dict(metadata or {})
    owner_id = _coerce_uuid(reseller_owner_org_id)
    if owner_id:
        payload[RESELLER_CONTACT_METADATA_KEY] = True
        payload[COMM_OWNER_ORG_ID_METADATA_KEY] = str(owner_id)
        payload[CONTACT_IDENTITY_MODE_METADATA_KEY] = CONTACT_IDENTITY_MODE_RESELLER_PROXY
    else:
        payload.pop(RESELLER_CONTACT_METADATA_KEY, None)
        payload.pop(COMM_OWNER_ORG_ID_METADATA_KEY, None)
        payload.pop(CONTACT_IDENTITY_MODE_METADATA_KEY, None)
    return payload or None
