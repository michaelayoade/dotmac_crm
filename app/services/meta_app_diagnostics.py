"""Read-only operational diagnostics for Meta app/webhook consistency."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.logging import get_logger
from app.models.connector import ConnectorConfig, ConnectorType
from app.models.domain_settings import SettingDomain
from app.models.integration import IntegrationTarget, IntegrationTargetType
from app.models.oauth_token import OAuthToken
from app.services import meta_oauth
from app.services.settings_spec import resolve_value

logger = get_logger(__name__)

Classification = Literal["matching_app", "app_mismatch", "missing_subscription", "insufficient_permissions"]
TokenStatus = Literal[
    "valid", "expired", "insufficient_permissions", "app_mismatch", "inaccessible_resource", "rate_limited"
]

REQUIRED_SCOPE_GROUPS: dict[str, set[str]] = {
    "instagram_messaging": {"instagram_manage_messages", "instagram_basic"},
    "messenger": {"pages_messaging", "pages_show_list"},
    "subscribed_apps_inspection": {"pages_show_list", "pages_read_engagement"},
    "webhook_ownership_diagnostics": {"pages_show_list", "pages_read_engagement"},
    "attribution_inspection": {"pages_read_engagement"},
    "identity_enrichment": {"instagram_basic", "pages_show_list"},
}


@dataclass
class RuntimeSecretReport:
    meta_app_id: str | None
    whatsapp_app_id: str | None
    meta_app_secret_fingerprint: str | None
    whatsapp_app_secret_fingerprint: str | None
    meta_app_secret_source: str
    whatsapp_app_secret_source: str


@dataclass
class SubscriptionApp:
    app_id: str | None
    name: str | None
    subscribed_fields: list[str]


@dataclass
class SubscriptionState:
    account_type: str
    account_id: str
    status: str
    subscribed_apps: list[SubscriptionApp]
    error: str | None = None


@dataclass
class AppConsistencyReport:
    classification: Classification
    configured_app_id: str | None
    instagram_account_id: str
    page_id: str | None
    instagram_state: SubscriptionState
    page_state: SubscriptionState | None


@dataclass
class TokenCapabilityReport:
    can_read_subscribed_apps: bool
    can_access_instagram: bool
    can_access_pages: bool
    can_access_messenger: bool
    can_access_webhooks: bool
    can_read_page_subscriptions: bool
    can_read_instagram_subscriptions: bool


@dataclass
class TokenInventoryEntry:
    token_label: str
    token_source: str
    token_type_guess: str
    token_fingerprint: str
    associated_app_id: str | None
    associated_page_id: str | None
    associated_instagram_account_id: str | None
    expires_at: str | None
    is_expired: bool | None
    scopes: list[str]
    can_read_subscribed_apps: bool
    can_access_instagram: bool
    can_access_pages: bool
    can_access_messenger: bool
    can_access_webhooks: bool
    can_read_page_subscriptions: bool
    can_read_instagram_subscriptions: bool
    status: TokenStatus
    missing_permissions: dict[str, list[str]]
    roles: list[str] = field(default_factory=list)
    duplicate_of: str | None = None
    associated_account_name: str | None = None
    connector_id: str | None = None
    connector_name: str | None = None
    account_type: str | None = None
    diagnostics_note: str | None = None


def fingerprint_secret(secret: str | None) -> str | None:
    if not secret:
        return None
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]


def _runtime_secret_source(db: Session, key: str, env_var: str) -> str:
    if resolve_value(db, SettingDomain.comms, key):
        return "domain_settings"
    if os.getenv(env_var):
        return "environment"
    return "missing"


def collect_runtime_secret_report(db: Session) -> RuntimeSecretReport:
    settings = meta_oauth.get_meta_settings(db)
    report = RuntimeSecretReport(
        meta_app_id=str(settings.get("meta_app_id")) if settings.get("meta_app_id") else None,
        whatsapp_app_id=str(settings.get("whatsapp_app_id")) if settings.get("whatsapp_app_id") else None,
        meta_app_secret_fingerprint=fingerprint_secret(settings.get("meta_app_secret")),
        whatsapp_app_secret_fingerprint=fingerprint_secret(settings.get("whatsapp_app_secret")),
        meta_app_secret_source=_runtime_secret_source(db, "meta_app_secret", "META_APP_SECRET"),
        whatsapp_app_secret_source=_runtime_secret_source(db, "whatsapp_app_secret", "WHATSAPP_APP_SECRET"),
    )
    logger.info(
        "instagram_signature_runtime_source meta_app_id=%s whatsapp_app_id=%s "
        "meta_app_secret_fingerprint=%s whatsapp_app_secret_fingerprint=%s "
        "meta_app_secret_source=%s whatsapp_app_secret_source=%s",
        report.meta_app_id,
        report.whatsapp_app_id,
        report.meta_app_secret_fingerprint,
        report.whatsapp_app_secret_fingerprint,
        report.meta_app_secret_source,
        report.whatsapp_app_secret_source,
    )
    return report


async def _graph_get(
    path: str,
    access_token: str,
    db: Session,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    timeout = meta_oauth._get_meta_api_timeout(db)
    base_url = meta_oauth._get_meta_graph_base_url(db).rstrip("/")
    request_params = dict(params or {})
    request_params["access_token"] = access_token
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{base_url}/{path.lstrip('/')}", params=request_params, timeout=timeout)
        response.raise_for_status()
        return response.json()


def _normalize_subscribed_apps(payload: dict[str, Any]) -> list[SubscriptionApp]:
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    normalized: list[SubscriptionApp] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        fields = item.get("subscribed_fields")
        subscribed_fields = [str(value) for value in fields] if isinstance(fields, list) else []
        normalized.append(
            SubscriptionApp(
                app_id=str(item.get("id")) if item.get("id") is not None else None,
                name=str(item.get("name")) if item.get("name") is not None else None,
                subscribed_fields=subscribed_fields,
            )
        )
    return normalized


async def _fetch_subscription_state(
    *,
    db: Session,
    account_type: str,
    account_id: str,
    token: OAuthToken | None,
) -> SubscriptionState:
    if token is None or not token.access_token:
        return SubscriptionState(
            account_type=account_type,
            account_id=account_id,
            status="insufficient_permissions",
            subscribed_apps=[],
            error="missing_access_token",
        )
    try:
        payload = await _graph_get(
            f"{account_id}/subscribed_apps",
            token.access_token,
            db,
            params={"fields": "id,name,subscribed_fields"},
        )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        error_text = exc.response.text[:200]
        derived_status = "insufficient_permissions" if status in {400, 401, 403} else "missing_subscription"
        return SubscriptionState(
            account_type=account_type,
            account_id=account_id,
            status=derived_status,
            subscribed_apps=[],
            error=f"http_{status}:{error_text}",
        )
    except httpx.HTTPError as exc:
        return SubscriptionState(
            account_type=account_type,
            account_id=account_id,
            status="missing_subscription",
            subscribed_apps=[],
            error=type(exc).__name__,
        )

    subscribed_apps = _normalize_subscribed_apps(payload)
    return SubscriptionState(
        account_type=account_type,
        account_id=account_id,
        status="ok" if subscribed_apps else "missing_subscription",
        subscribed_apps=subscribed_apps,
    )


def _log_subscription_state(state: SubscriptionState) -> None:
    logger.info(
        "meta_webhook_subscription_state account_type=%s account_id=%s status=%s app_ids=%s subscribed_fields=%s error=%s",
        state.account_type,
        state.account_id,
        state.status,
        [app.app_id for app in state.subscribed_apps],
        [app.subscribed_fields for app in state.subscribed_apps],
        state.error,
    )


def _classify_consistency(
    configured_app_id: str | None,
    instagram_state: SubscriptionState,
    page_state: SubscriptionState | None,
) -> Classification:
    if instagram_state.status == "insufficient_permissions" or (
        page_state is not None and page_state.status == "insufficient_permissions"
    ):
        return "insufficient_permissions"
    if not configured_app_id:
        return "missing_subscription"

    instagram_app_ids = {app.app_id for app in instagram_state.subscribed_apps if app.app_id}
    page_app_ids = {app.app_id for app in page_state.subscribed_apps if app.app_id} if page_state else set()

    if configured_app_id in instagram_app_ids and (page_state is None or configured_app_id in page_app_ids):
        return "matching_app"
    if not instagram_app_ids:
        return "missing_subscription"
    return "app_mismatch"


async def check_meta_app_consistency(
    db: Session,
    *,
    instagram_account_id: str,
    page_id: str | None,
) -> AppConsistencyReport:
    runtime = collect_runtime_secret_report(db)
    instagram_token = meta_oauth.get_token_for_instagram(db, instagram_account_id)
    page_token = meta_oauth.get_token_for_page(db, page_id) if page_id else None

    instagram_state = await _fetch_subscription_state(
        db=db,
        account_type="instagram_business",
        account_id=instagram_account_id,
        token=instagram_token,
    )
    page_state = None
    if page_id:
        page_state = await _fetch_subscription_state(
            db=db,
            account_type="page",
            account_id=page_id,
            token=page_token,
        )

    _log_subscription_state(instagram_state)
    if page_state is not None:
        _log_subscription_state(page_state)

    classification = _classify_consistency(runtime.meta_app_id, instagram_state, page_state)
    logger.info(
        "meta_app_consistency_check classification=%s app_id=%s instagram_account_id=%s page_id=%s "
        "instagram_subscribed_fields=%s page_subscribed_fields=%s",
        classification,
        runtime.meta_app_id,
        instagram_account_id,
        page_id,
        [app.subscribed_fields for app in instagram_state.subscribed_apps],
        [app.subscribed_fields for app in page_state.subscribed_apps] if page_state else [],
    )
    return AppConsistencyReport(
        classification=classification,
        configured_app_id=runtime.meta_app_id,
        instagram_account_id=instagram_account_id,
        page_id=page_id,
        instagram_state=instagram_state,
        page_state=page_state,
    )


def _serialize_consistency_report(runtime: RuntimeSecretReport, consistency: AppConsistencyReport) -> dict[str, Any]:
    return {
        "runtime": asdict(runtime),
        "consistency": asdict(consistency),
        "instagram_subscription_attached_to_configured_app": consistency.classification == "matching_app",
    }


def _serialize_token_inventory(inventory: list[TokenInventoryEntry]) -> dict[str, Any]:
    best_token = next(
        (
            entry.token_label
            for entry in inventory
            if entry.can_read_instagram_subscriptions and entry.status == "valid"
        ),
        None,
    )
    return {
        "summary": {
            "total_tokens": len(inventory),
            "valid_tokens": sum(1 for entry in inventory if entry.status == "valid"),
            "override_tokens": sum(1 for entry in inventory if "override" in entry.roles),
            "duplicate_tokens": sum(1 for entry in inventory if entry.duplicate_of),
            "best_diagnostics_token": best_token,
        },
        "items": [asdict(entry) for entry in inventory],
    }


def _isoformat(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def _token_status_from_debug(debug_payload: dict[str, Any]) -> TokenStatus:
    data = debug_payload.get("data")
    if not isinstance(data, dict):
        return "inaccessible_resource"
    if not data.get("is_valid", False):
        return "expired" if data.get("expires_at") else "insufficient_permissions"
    return "valid"


def _debug_scopes(debug_payload: dict[str, Any]) -> list[str]:
    data = debug_payload.get("data")
    if not isinstance(data, dict):
        return []
    scopes = data.get("scopes")
    if isinstance(scopes, list):
        return [str(scope) for scope in scopes]
    granular = data.get("granular_scopes")
    if isinstance(granular, list):
        values: list[str] = []
        for item in granular:
            if isinstance(item, dict) and item.get("scope"):
                values.append(str(item["scope"]))
        return values
    return []


def _missing_permissions(scopes: list[str]) -> dict[str, list[str]]:
    granted = set(scopes)
    return {
        label: sorted(required - granted) for label, required in REQUIRED_SCOPE_GROUPS.items() if required - granted
    }


async def _debug_meta_token(
    db: Session,
    *,
    input_token: str,
    app_id: str | None,
    app_secret: str | None,
) -> dict[str, Any]:
    if not app_id or not app_secret:
        return {"data": {"is_valid": False, "error": {"message": "missing_app_credentials"}}}
    app_access_token = f"{app_id}|{app_secret}"
    try:
        return await _graph_get(
            "debug_token",
            app_access_token,
            db,
            params={"input_token": input_token},
        )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        return {
            "data": {
                "is_valid": False,
                "error": {"message": exc.response.text[:200], "status": status},
            }
        }
    except httpx.HTTPError as exc:
        return {
            "data": {
                "is_valid": False,
                "error": {"message": type(exc).__name__},
            }
        }


async def _probe_endpoint(
    db: Session,
    *,
    path: str,
    access_token: str,
    params: dict[str, str] | None = None,
) -> tuple[bool, str | None]:
    try:
        await _graph_get(path, access_token, db, params=params)
        return True, None
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 429:
            return False, "rate_limited"
        if status in {400, 401, 403}:
            return False, "insufficient_permissions"
        if status == 404:
            return False, "inaccessible_resource"
        return False, f"http_{status}"
    except httpx.HTTPError as exc:
        return False, type(exc).__name__


def _status_from_capabilities(
    *,
    base_status: TokenStatus,
    capability_errors: list[str | None],
    associated_app_id: str | None,
    configured_app_id: str | None,
) -> TokenStatus:
    if base_status != "valid":
        return base_status
    if associated_app_id and configured_app_id and associated_app_id != configured_app_id:
        return "app_mismatch"
    if any(error == "rate_limited" for error in capability_errors):
        return "rate_limited"
    if any(error == "insufficient_permissions" for error in capability_errors):
        return "insufficient_permissions"
    if any(error == "inaccessible_resource" for error in capability_errors):
        return "inaccessible_resource"
    return "valid"


def _write_stdout(text: str) -> None:
    sys.stdout.write(text)


def _token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _trim_token(value: object | None) -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip()
    return token or None


def _generic_override_token(db: Session) -> str | None:
    return _trim_token(resolve_value(db, SettingDomain.comms, "meta_access_token_override"))


def _facebook_override_token(db: Session) -> str | None:
    return _trim_token(resolve_value(db, SettingDomain.comms, "meta_facebook_access_token_override"))


def _instagram_override_token(db: Session) -> str | None:
    return _trim_token(resolve_value(db, SettingDomain.comms, "meta_instagram_access_token_override"))


def _meta_targets(db: Session) -> list[IntegrationTarget]:
    return (
        db.query(IntegrationTarget)
        .join(ConnectorConfig, ConnectorConfig.id == IntegrationTarget.connector_config_id)
        .filter(IntegrationTarget.target_type == IntegrationTargetType.crm)
        .filter(IntegrationTarget.is_active.is_(True))
        .filter(
            ConnectorConfig.connector_type.in_(
                [ConnectorType.facebook, ConnectorType.instagram, ConnectorType.whatsapp]
            )
        )
        .all()
    )


def _inventory_connector_auth_tokens(db: Session) -> list[dict[str, Any]]:
    connectors = (
        db.query(ConnectorConfig)
        .filter(
            ConnectorConfig.connector_type.in_(
                [ConnectorType.facebook, ConnectorType.instagram, ConnectorType.whatsapp]
            )
        )
        .all()
    )
    rows: list[dict[str, Any]] = []
    for connector in connectors:
        auth_config = connector.auth_config if isinstance(connector.auth_config, dict) else {}
        token_value = _trim_token(auth_config.get("access_token") or auth_config.get("token"))
        if not token_value:
            continue
        rows.append(
            {
                "token_label": f"connector:{connector.name}",
                "token_source": "connector_auth_config",  # nosec B105
                "token_type_guess": f"{connector.connector_type.value}_connector_auth",
                "token_value": token_value,
                "connector_id": str(connector.id),
                "connector_name": connector.name,
                "account_type": connector.connector_type.value,
                "associated_page_id": None,
                "associated_instagram_account_id": None,
                "associated_account_name": None,
                "roles": ["fallback"] if connector.connector_type == ConnectorType.whatsapp else ["override"],
            }
        )
    return rows


def _inventory_override_tokens(db: Session) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    generic = _generic_override_token(db)
    if generic:
        rows.append(
            {
                "token_label": "settings:meta_access_token_override",  # nosec B105
                "token_source": "domain_settings",  # nosec B105
                "token_type_guess": "generic_meta_override",  # nosec B105
                "token_value": generic,
                "roles": ["override", "unused"],
                "account_type": None,
                "associated_page_id": None,
                "associated_instagram_account_id": None,
                "associated_account_name": None,
                "connector_id": None,
                "connector_name": None,
            }
        )
    fb = _facebook_override_token(db)
    if fb:
        rows.append(
            {
                "token_label": "settings:meta_facebook_access_token_override",  # nosec B105
                "token_source": "domain_settings",  # nosec B105
                "token_type_guess": "facebook_override",  # nosec B105
                "token_value": fb,
                "roles": ["override"],
                "account_type": "page",
                "associated_page_id": None,
                "associated_instagram_account_id": None,
                "associated_account_name": None,
                "connector_id": None,
                "connector_name": None,
            }
        )
    ig = _instagram_override_token(db)
    if ig:
        rows.append(
            {
                "token_label": "settings:meta_instagram_access_token_override",  # nosec B105
                "token_source": "domain_settings",  # nosec B105
                "token_type_guess": "instagram_override"
                if not ig.upper().startswith("IG")
                else "instagram_login_override",
                "token_value": ig,
                "roles": ["override"],
                "account_type": "instagram_business",
                "associated_page_id": None,
                "associated_instagram_account_id": None,
                "associated_account_name": None,
                "connector_id": None,
                "connector_name": None,
            }
        )
    return rows


def _inventory_oauth_tokens(db: Session) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    tokens = db.query(OAuthToken).filter(OAuthToken.provider == "meta").all()
    for token in tokens:
        rows.append(
            {
                "token_label": f"oauth:{token.account_type}:{token.external_account_id}",
                "token_source": "oauth_tokens",  # nosec B105
                "token_type_guess": token.account_type,
                "token_value": _trim_token(token.access_token),
                "connector_id": str(token.connector_config_id),
                "connector_name": token.connector_config.name if token.connector_config else None,
                "account_type": token.account_type,
                "associated_page_id": token.external_account_id if token.account_type == "page" else None,
                "associated_instagram_account_id": token.external_account_id
                if token.account_type == "instagram_business"
                else None,
                "associated_account_name": token.external_account_name,
                "expires_at_dt": token.token_expires_at,
                "is_expired_db": token.is_token_expired(),
                "scopes_db": [str(scope) for scope in token.scopes] if isinstance(token.scopes, list) else [],
                "roles": ["primary"] if token.is_active else ["unused"],
                "metadata": token.metadata_ if isinstance(token.metadata_, dict) else {},
            }
        )
    return [row for row in rows if row.get("token_value")]


def _dedupe_roles(entries: list[TokenInventoryEntry]) -> None:
    seen: dict[str, str] = {}
    for entry in entries:
        fingerprint = entry.token_fingerprint
        if fingerprint in seen:
            entry.duplicate_of = seen[fingerprint]
            if "duplicate" not in entry.roles:
                entry.roles.append("duplicate")
        else:
            seen[fingerprint] = entry.token_label


async def audit_meta_tokens(
    db: Session,
    *,
    instagram_account_id: str,
    page_id: str | None,
) -> list[TokenInventoryEntry]:
    runtime = collect_runtime_secret_report(db)
    raw_entries = _inventory_override_tokens(db) + _inventory_connector_auth_tokens(db) + _inventory_oauth_tokens(db)
    inventory: list[TokenInventoryEntry] = []
    for row in raw_entries:
        token_value = row["token_value"]
        fingerprint = _token_fingerprint(token_value)
        logger.info(
            "meta_token_inventory token_label=%s token_source=%s token_type_guess=%s token_fingerprint=%s roles=%s",
            row["token_label"],
            row["token_source"],
            row["token_type_guess"],
            fingerprint,
            row.get("roles", []),
        )
        debug_payload = await _debug_meta_token(
            db,
            input_token=token_value,
            app_id=runtime.meta_app_id,
            app_secret=meta_oauth.get_meta_settings(db).get("meta_app_secret"),
        )
        scopes = sorted(set(_debug_scopes(debug_payload)) | set(row.get("scopes_db", [])))
        data_obj = debug_payload.get("data")
        data = data_obj if isinstance(data_obj, dict) else {}
        app_id_value = data.get("app_id")
        associated_app_id = str(app_id_value) if app_id_value is not None else runtime.meta_app_id
        expires_at = None
        is_expired = row.get("is_expired_db")
        expires_at_raw = data.get("expires_at")
        if isinstance(expires_at_raw, int):
            expires_dt = datetime.fromtimestamp(expires_at_raw, tz=UTC)
            expires_at = _isoformat(expires_dt)
            is_expired = expires_dt <= datetime.now(UTC)
        elif row.get("expires_at_dt") is not None:
            expires_at = _isoformat(row["expires_at_dt"])
            is_expired = bool(row.get("is_expired_db"))

        can_access_pages, page_error = await _probe_endpoint(
            db,
            path="me/accounts",
            access_token=token_value,
            params={"fields": "id,name"},
        )
        can_access_instagram, instagram_error = await _probe_endpoint(
            db,
            path=instagram_account_id,
            access_token=token_value,
            params={"fields": "id,username"},
        )
        can_read_page_subscriptions = False
        page_subscription_error = None
        if page_id:
            can_read_page_subscriptions, page_subscription_error = await _probe_endpoint(
                db,
                path=f"{page_id}/subscribed_apps",
                access_token=token_value,
                params={"fields": "id,name,subscribed_fields"},
            )
        can_read_instagram_subscriptions, instagram_subscription_error = await _probe_endpoint(
            db,
            path=f"{instagram_account_id}/subscribed_apps",
            access_token=token_value,
            params={"fields": "id,name,subscribed_fields"},
        )
        capability_errors = [page_error, instagram_error, page_subscription_error, instagram_subscription_error]
        status = _status_from_capabilities(
            base_status=_token_status_from_debug(debug_payload),
            capability_errors=capability_errors,
            associated_app_id=associated_app_id,
            configured_app_id=runtime.meta_app_id,
        )
        capabilities = TokenCapabilityReport(
            can_read_subscribed_apps=can_read_page_subscriptions or can_read_instagram_subscriptions,
            can_access_instagram=can_access_instagram,
            can_access_pages=can_access_pages,
            can_access_messenger=("pages_messaging" in scopes) and can_access_pages,
            can_access_webhooks=can_read_page_subscriptions or can_read_instagram_subscriptions,
            can_read_page_subscriptions=can_read_page_subscriptions,
            can_read_instagram_subscriptions=can_read_instagram_subscriptions,
        )
        entry = TokenInventoryEntry(
            token_label=row["token_label"],
            token_source=row["token_source"],
            token_type_guess=row["token_type_guess"],
            token_fingerprint=fingerprint,
            associated_app_id=associated_app_id,
            associated_page_id=row.get("associated_page_id"),
            associated_instagram_account_id=row.get("associated_instagram_account_id"),
            expires_at=expires_at,
            is_expired=is_expired,
            scopes=scopes,
            can_read_subscribed_apps=capabilities.can_read_subscribed_apps,
            can_access_instagram=capabilities.can_access_instagram,
            can_access_pages=capabilities.can_access_pages,
            can_access_messenger=capabilities.can_access_messenger,
            can_access_webhooks=capabilities.can_access_webhooks,
            can_read_page_subscriptions=capabilities.can_read_page_subscriptions,
            can_read_instagram_subscriptions=capabilities.can_read_instagram_subscriptions,
            status=status,
            missing_permissions=_missing_permissions(scopes),
            roles=list(row.get("roles", [])),
            associated_account_name=row.get("associated_account_name"),
            connector_id=row.get("connector_id"),
            connector_name=row.get("connector_name"),
            account_type=row.get("account_type"),
            diagnostics_note=";".join(error for error in capability_errors if error) or None,
        )
        logger.info(
            "meta_token_scope_audit token_label=%s token_fingerprint=%s status=%s scopes=%s associated_app_id=%s",
            entry.token_label,
            entry.token_fingerprint,
            entry.status,
            entry.scopes,
            entry.associated_app_id,
        )
        if entry.missing_permissions:
            logger.info(
                "meta_token_permission_gap token_label=%s token_fingerprint=%s missing_permissions=%s",
                entry.token_label,
                entry.token_fingerprint,
                entry.missing_permissions,
            )
        if "override" in entry.roles:
            logger.info(
                "meta_token_override_detected token_label=%s token_fingerprint=%s token_type_guess=%s",
                entry.token_label,
                entry.token_fingerprint,
                entry.token_type_guess,
            )
        inventory.append(entry)

    _dedupe_roles(inventory)
    return inventory


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Meta app/webhook consistency diagnostic")
    parser.add_argument("--instagram-account-id", required=True)
    parser.add_argument("--page-id")
    parser.add_argument("--mode", choices=["consistency", "inventory", "both"], default="both")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        runtime = collect_runtime_secret_report(db)
        consistency: AppConsistencyReport | None = None
        inventory: list[TokenInventoryEntry] | None = None
        if args.mode in {"consistency", "both"}:
            consistency = asyncio.run(
                check_meta_app_consistency(
                    db,
                    instagram_account_id=args.instagram_account_id,
                    page_id=args.page_id,
                )
            )
        if args.mode in {"inventory", "both"}:
            inventory = asyncio.run(
                audit_meta_tokens(
                    db,
                    instagram_account_id=args.instagram_account_id,
                    page_id=args.page_id,
                )
            )
        payload: dict[str, Any] = {"runtime": asdict(runtime)}
        if consistency is not None:
            payload.update(_serialize_consistency_report(runtime, consistency))
        if inventory is not None:
            payload["token_inventory"] = _serialize_token_inventory(inventory)
        if args.json:
            _write_stdout(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        else:
            lines = [
                f"configured meta_app_id: {runtime.meta_app_id}",
                f"configured whatsapp_app_id: {runtime.whatsapp_app_id}",
                f"meta_app_secret_fingerprint: {runtime.meta_app_secret_fingerprint}",
                f"whatsapp_app_secret_fingerprint: {runtime.whatsapp_app_secret_fingerprint}",
                f"meta_app_secret_source: {runtime.meta_app_secret_source}",
                f"whatsapp_app_secret_source: {runtime.whatsapp_app_secret_source}",
            ]
            if consistency is not None:
                lines.extend(
                    [
                        f"classification: {consistency.classification}",
                        f"instagram_account_id: {consistency.instagram_account_id}",
                        f"page_id: {consistency.page_id}",
                        f"instagram webhook attached to configured app: {consistency.classification == 'matching_app'}",
                    ]
                )
            if inventory is not None:
                summary = _serialize_token_inventory(inventory)["summary"]
                lines.extend(
                    [
                        f"total_tokens: {summary['total_tokens']}",
                        f"valid_tokens: {summary['valid_tokens']}",
                        f"override_tokens: {summary['override_tokens']}",
                        f"duplicate_tokens: {summary['duplicate_tokens']}",
                        f"best_diagnostics_token: {summary['best_diagnostics_token']}",
                    ]
                )
            _write_stdout("\n".join(lines) + "\n")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
