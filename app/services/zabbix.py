from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.connector import ConnectorAuthType, ConnectorConfig, ConnectorType
from app.services.auth_flow import _decrypt_secret, _encrypt_secret

logger = logging.getLogger(__name__)

_UP_AVAILABILITY = {"1"}
_DOWN_AVAILABILITY = {"2"}


class ZabbixApiError(RuntimeError):
    def __init__(self, message: str, *, unauthorized: bool = False):
        super().__init__(message)
        self.unauthorized = unauthorized


def _active_connector(db: Session) -> ConnectorConfig | None:
    return db.execute(
        select(ConnectorConfig)
        .where(
            ConnectorConfig.connector_type == ConnectorType.zabbix,
            ConnectorConfig.is_active.is_(True),
        )
        .order_by(ConnectorConfig.updated_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _resolve_api_url(base_url: str | None) -> str:
    raw = str(base_url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    path = parsed.path or ""
    if path.endswith("/api_jsonrpc.php"):
        api_path = path
    elif "/zabbix.php" in path:
        api_path = path.split("/zabbix.php", 1)[0].rstrip("/") + "/api_jsonrpc.php"
    else:
        api_path = path.rstrip("/") + "/api_jsonrpc.php"
    return parsed._replace(path=api_path, params="", query="", fragment="").geturl()


def _resolve_timeout(config: ConnectorConfig) -> int:
    try:
        timeout = int(config.timeout_sec or 15)
    except (TypeError, ValueError):
        timeout = 15
    return max(timeout, 5)


def _token_from_config(config: ConnectorConfig) -> str | None:
    auth_config = config.auth_config if isinstance(config.auth_config, dict) else {}
    headers = config.headers if isinstance(config.headers, dict) else {}

    encrypted_token = str(auth_config.get("token_enc") or "").strip()
    if encrypted_token:
        return _decrypt_secret(None, encrypted_token)

    for key in ("token", "api_token", "access_token", "bearer_token"):
        value = str(auth_config.get(key) or "").strip()
        if value:
            return value

    authorization = str(headers.get("Authorization") or headers.get("authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip() or None
    return None


def _login_credentials(config: ConnectorConfig) -> tuple[str, str] | None:
    auth_config = config.auth_config if isinstance(config.auth_config, dict) else {}
    username = str(auth_config.get("username") or auth_config.get("user") or "").strip()
    encrypted_password = str(auth_config.get("password_enc") or "").strip()
    if encrypted_password:
        password = _decrypt_secret(None, encrypted_password)
    else:
        password = str(auth_config.get("password") or auth_config.get("pass") or "").strip()
    if username and password:
        return username, password
    return None


def _rpc_call(
    api_url: str,
    *,
    method: str,
    params: dict[str, Any] | list[Any],
    timeout: int,
    auth_token: str | None = None,
    bearer_token: str | None = None,
) -> dict[str, Any]:
    import requests

    headers = {"Content-Type": "application/json-rpc"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": 1,
    }
    if auth_token:
        payload["auth"] = auth_token

    response = requests.post(api_url, json=payload, headers=headers, timeout=timeout)
    response.raise_for_status()
    result = response.json()
    if isinstance(result, dict) and result.get("error"):
        error = result["error"]
        message = str(error.get("data") or error.get("message") or error)
        unauthorized = "not authorized" in message.lower()
        raise ZabbixApiError(message, unauthorized=unauthorized)
    if not isinstance(result, dict):
        raise ZabbixApiError("Unexpected Zabbix response shape")
    return result


def _session_auth_token(api_url: str, config: ConnectorConfig, timeout: int) -> str | None:
    credentials = _login_credentials(config)
    if not credentials:
        return None
    username, password = credentials
    result = _rpc_call(
        api_url,
        method="user.login",
        params={"username": username, "password": password},
        timeout=timeout,
    )
    token = result.get("result")
    return str(token).strip() if token is not None else None


def configure_connector(
    db: Session,
    connector: ConnectorConfig,
    *,
    name: str,
    base_url: str,
    username: str,
    password: str | None,
    timeout_sec: int | None,
    notes: str | None,
    is_active: bool,
) -> ConnectorConfig:
    auth_config = dict(connector.auth_config or {}) if isinstance(connector.auth_config, dict) else {}

    normalized_name = str(name or "").strip() or connector.name
    normalized_username = str(username or "").strip()
    normalized_url = _resolve_api_url(base_url)
    credentials_changed = False

    if auth_config.get("username") != normalized_username:
        credentials_changed = True
    if password:
        auth_config["password_enc"] = _encrypt_secret(db, password)
        auth_config.pop("password", None)
        credentials_changed = True

    auth_config["username"] = normalized_username
    if credentials_changed or connector.base_url != normalized_url:
        auth_config.pop("token", None)
        auth_config.pop("token_enc", None)
        auth_config.pop("token_last_refreshed_at", None)

    connector.name = normalized_name
    connector.connector_type = ConnectorType.zabbix
    connector.auth_type = ConnectorAuthType.basic
    connector.base_url = normalized_url
    connector.timeout_sec = timeout_sec
    connector.notes = str(notes or "").strip() or None
    connector.is_active = is_active
    connector.auth_config = auth_config
    db.add(connector)
    db.commit()
    db.refresh(connector)

    if normalized_username and (password or _login_credentials(connector)):
        refresh_api_token(db, connector)
    return connector


def refresh_api_token(db: Session, connector: ConnectorConfig) -> str | None:
    api_url = _resolve_api_url(connector.base_url)
    if not api_url:
        return None
    timeout = _resolve_timeout(connector)
    token = _session_auth_token(api_url, connector, timeout)
    if not token:
        return None
    auth_config = dict(connector.auth_config or {}) if isinstance(connector.auth_config, dict) else {}
    auth_config["token_enc"] = _encrypt_secret(db, token)
    auth_config.pop("token", None)
    auth_config["token_last_refreshed_at"] = datetime.now(UTC).isoformat()
    connector.auth_config = auth_config
    db.add(connector)
    db.commit()
    db.refresh(connector)
    return token


def ensure_api_token(db: Session, connector: ConnectorConfig) -> str | None:
    token = _token_from_config(connector)
    if token:
        return token
    return refresh_api_token(db, connector)


def _availability_state(value: object | None) -> str | None:
    normalized = str(value or "").strip()
    if normalized in _UP_AVAILABILITY:
        return "up"
    if normalized in _DOWN_AVAILABILITY:
        return "down"
    return None


def fetch_monitoring_devices(db: Session) -> list[dict[str, Any]]:
    """Fetch monitoring device rows from Zabbix and normalize them for station matching."""
    config = _active_connector(db)
    if config is None:
        return []

    api_url = _resolve_api_url(config.base_url)
    if not api_url:
        logger.warning("zabbix_config_incomplete reason=missing_base_url")
        return []

    timeout = _resolve_timeout(config)
    auth_token = ensure_api_token(db, config)
    if not auth_token:
        logger.warning("zabbix_config_incomplete reason=missing_auth")
        return []

    result = None
    for attempt in range(2):
        try:
            result = _rpc_call(
                api_url,
                method="host.get",
                params={
                    "output": ["hostid", "host", "name", "status", "available", "snmp_available"],
                    "selectInterfaces": ["interfaceid", "type", "available"],
                },
                timeout=timeout,
                auth_token=auth_token,
            )
            break
        except ZabbixApiError as exc:
            if exc.unauthorized and attempt == 0:
                auth_token = refresh_api_token(db, config)
                if auth_token:
                    continue
            logger.error("zabbix_fetch_monitoring_failed error=%s", str(exc))
            return []
        except Exception as exc:
            logger.error("zabbix_fetch_monitoring_failed error=%s", str(exc))
            return []

    if result is None:
        return []
    hosts = result.get("result")
    if not isinstance(hosts, list):
        return []

    rows: list[dict[str, Any]] = []
    for host in hosts:
        if not isinstance(host, dict):
            continue
        title = str(host.get("name") or host.get("host") or "").strip()
        if not title:
            continue

        ping_state = _availability_state(host.get("available"))
        snmp_state = _availability_state(host.get("snmp_available"))
        if snmp_state is None:
            interfaces = host.get("interfaces")
            if isinstance(interfaces, list):
                for interface in interfaces:
                    if not isinstance(interface, dict):
                        continue
                    if str(interface.get("type") or "").strip() == "2":
                        snmp_state = _availability_state(interface.get("available"))
                        if snmp_state is not None:
                            break

        rows.append(
            {
                "id": str(host.get("hostid") or "").strip(),
                "title": title,
                "name": title,
                "ping_state": ping_state or "unknown",
                "snmp_state": snmp_state or "unknown",
                "status": str(host.get("status") or "").strip(),
                "source": "zabbix",
            }
        )
    return rows
