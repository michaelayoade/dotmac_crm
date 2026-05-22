from __future__ import annotations

from dataclasses import dataclass
from itertools import count
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.models.connector import ConnectorAuthType, ConnectorConfig, ConnectorType

DEFAULT_LINK_DOWN_TERMS = (
    "link down",
    "link is down",
    "interface down",
    "interface is down",
    "operational status is down",
    "port down",
    "disconnection",
    "outage",
    "unavailable",
    "not available",
)


class ZabbixConnectorError(Exception):
    pass


class ZabbixConnectorConfigurationError(ZabbixConnectorError):
    pass


class ZabbixConnectorRequestError(ZabbixConnectorError):
    pass


@dataclass(frozen=True)
class ZabbixProblem:
    event_id: str | None
    object_id: str | None
    name: str
    severity: str | None
    clock: str | None
    host_id: str | None
    host_name: str | None
    tags: list[dict[str, Any]]
    raw: dict[str, Any]


def normalize_zabbix_api_url(base_url: str | None) -> str:
    value = (base_url or "").strip()
    if not value:
        raise ZabbixConnectorConfigurationError("Zabbix connector base_url is not configured")
    marker = "/zabbix/"
    if marker in value and not value.endswith("/api_jsonrpc.php"):
        return value[: value.index(marker) + len(marker)] + "api_jsonrpc.php"
    return value


def get_active_zabbix_connector(db: Session) -> ConnectorConfig:
    connector = (
        db.query(ConnectorConfig)
        .filter(ConnectorConfig.connector_type == ConnectorType.zabbix)
        .filter(ConnectorConfig.is_active.is_(True))
        .order_by(ConnectorConfig.updated_at.desc(), ConnectorConfig.created_at.desc())
        .first()
    )
    if connector:
        return connector

    legacy = (
        db.query(ConnectorConfig)
        .filter(ConnectorConfig.is_active.is_(True))
        .filter((ConnectorConfig.name.ilike("%zabbix%")) | (ConnectorConfig.base_url.ilike("%/zabbix/%")))
        .order_by(ConnectorConfig.updated_at.desc(), ConnectorConfig.created_at.desc())
        .first()
    )
    if not legacy:
        raise ZabbixConnectorConfigurationError("No active Zabbix connector is configured")
    return legacy


def extract_zabbix_api_token(connector: ConnectorConfig) -> str:
    auth_config = connector.auth_config if isinstance(connector.auth_config, dict) else {}
    headers = connector.headers if isinstance(connector.headers, dict) else {}

    for key in ("api_key", "token", "bearer_token"):
        value = str(auth_config.get(key) or "").strip()
        if value:
            return value

    authorization = str(headers.get("Authorization") or headers.get("authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    if connector.auth_type == ConnectorAuthType.bearer and authorization:
        return authorization

    raise ZabbixConnectorConfigurationError("Zabbix connector API token is not configured")


class ZabbixConnectorClient:
    def __init__(self, connector: ConnectorConfig) -> None:
        self.api_url = normalize_zabbix_api_url(connector.base_url)
        self.api_token = extract_zabbix_api_token(connector)
        self.timeout = connector.timeout_sec or 20
        self._request_ids = count(1)

    def _request(self, method: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": next(self._request_ids),
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    self.api_url,
                    headers={
                        "Authorization": f"Bearer {self.api_token}",
                        "Content-Type": "application/json-rpc",
                    },
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ZabbixConnectorRequestError("Zabbix API request failed") from exc

        if not isinstance(data, dict):
            raise ZabbixConnectorRequestError("Invalid Zabbix API response")
        if data.get("error"):
            error = data.get("error") or {}
            message = error.get("data") or error.get("message") or "Zabbix API returned an error"
            raise ZabbixConnectorRequestError(str(message))

        result = data.get("result")
        if not isinstance(result, list):
            raise ZabbixConnectorRequestError("Invalid Zabbix API result")
        return [item for item in result if isinstance(item, dict)]

    def get_active_problems(self, limit: int = 1000) -> list[dict[str, Any]]:
        return self._request(
            "problem.get",
            {
                "output": "extend",
                "selectHosts": ["hostid", "host", "name"],
                "selectTags": "extend",
                "recent": "false",
                "sortfield": ["eventid"],
                "sortorder": "DESC",
                "limit": limit,
            },
        )


def _problem_matches_terms(problem: dict[str, Any], terms: tuple[str, ...]) -> bool:
    haystack_parts = [
        str(problem.get("name") or ""),
        str(problem.get("opdata") or ""),
    ]
    for tag in problem.get("tags") or []:
        if isinstance(tag, dict):
            haystack_parts.append(str(tag.get("tag") or ""))
            haystack_parts.append(str(tag.get("value") or ""))
    haystack = " ".join(haystack_parts).lower()
    return any(term in haystack for term in terms)


def map_problem(problem: dict[str, Any]) -> ZabbixProblem:
    hosts = [host for host in problem.get("hosts") or [] if isinstance(host, dict)]
    host = hosts[0] if hosts else {}
    return ZabbixProblem(
        event_id=str(problem.get("eventid") or "") or None,
        object_id=str(problem.get("objectid") or "") or None,
        name=str(problem.get("name") or ""),
        severity=str(problem.get("severity") or "") or None,
        clock=str(problem.get("clock") or "") or None,
        host_id=str(host.get("hostid") or "") or None,
        host_name=str(host.get("name") or host.get("host") or "") or None,
        tags=[tag for tag in problem.get("tags") or [] if isinstance(tag, dict)],
        raw=problem,
    )


def list_live_link_down_problems(
    db: Session,
    *,
    limit: int = 1000,
    terms: tuple[str, ...] = DEFAULT_LINK_DOWN_TERMS,
) -> list[ZabbixProblem]:
    connector = get_active_zabbix_connector(db)
    client = ZabbixConnectorClient(connector)
    problems = client.get_active_problems(limit=limit)
    return [map_problem(problem) for problem in problems if _problem_matches_terms(problem, terms)]
