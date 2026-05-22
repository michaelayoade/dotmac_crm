from __future__ import annotations

import httpx
import pytest

from app.models.connector import ConnectorAuthType, ConnectorType
from app.schemas.connector import ConnectorConfigCreate
from app.services import connector as connector_service
from app.services.zabbix_connector import (
    ZabbixConnectorClient,
    ZabbixConnectorConfigurationError,
    extract_zabbix_api_token,
    list_live_link_down_problems,
    normalize_zabbix_api_url,
)


def test_normalizes_dashboard_url_to_api_url():
    assert (
        normalize_zabbix_api_url("http://160.119.127.193/zabbix/zabbix.php?action=dashboard.view")
        == "http://160.119.127.193/zabbix/api_jsonrpc.php"
    )


def test_extracts_api_token_from_auth_config(db_session):
    config = connector_service.connector_configs.create(
        db_session,
        ConnectorConfigCreate(
            name="Zabbix Token",
            connector_type=ConnectorType.zabbix,
            auth_type=ConnectorAuthType.api_key,
            base_url="https://zabbix.example.com/api_jsonrpc.php",
            auth_config={"api_key": "secret-token"},
        ),
    )

    assert extract_zabbix_api_token(config) == "secret-token"


def test_missing_api_token_raises_configuration_error(db_session):
    config = connector_service.connector_configs.create(
        db_session,
        ConnectorConfigCreate(
            name="Zabbix Missing Token",
            connector_type=ConnectorType.zabbix,
            auth_type=ConnectorAuthType.none,
            base_url="https://zabbix.example.com/api_jsonrpc.php",
        ),
    )

    with pytest.raises(ZabbixConnectorConfigurationError):
        extract_zabbix_api_token(config)


def test_lists_live_link_down_problems_from_configured_connector(db_session, monkeypatch):
    connector_service.connector_configs.create(
        db_session,
        ConnectorConfigCreate(
            name="Zabbix",
            connector_type=ConnectorType.zabbix,
            auth_type=ConnectorAuthType.api_key,
            base_url="https://zabbix.example.com/api_jsonrpc.php",
            auth_config={"api_key": "secret-token"},
        ),
    )

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, headers, json):
            assert url == "https://zabbix.example.com/api_jsonrpc.php"
            assert headers["Authorization"] == "Bearer secret-token"
            assert json["method"] == "problem.get"
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "result": [
                        {
                            "eventid": "100",
                            "objectid": "200",
                            "name": "Interface ether1 link down",
                            "severity": "4",
                            "clock": "1770000000",
                            "hosts": [{"hostid": "300", "name": "Gudu Access"}],
                            "tags": [],
                        },
                        {
                            "eventid": "101",
                            "objectid": "201",
                            "name": "High CPU utilization",
                            "severity": "3",
                            "clock": "1770000001",
                            "hosts": [{"hostid": "301", "name": "Garki Core"}],
                            "tags": [],
                        },
                    ],
                },
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr("app.services.zabbix_connector.httpx.Client", FakeClient)

    problems = list_live_link_down_problems(db_session)

    assert len(problems) == 1
    assert problems[0].event_id == "100"
    assert problems[0].host_name == "Gudu Access"
    assert problems[0].name == "Interface ether1 link down"


def test_legacy_zabbix_dashboard_connector_is_supported(db_session, monkeypatch):
    connector_service.connector_configs.create(
        db_session,
        ConnectorConfigCreate(
            name="Zabbix",
            connector_type=ConnectorType.custom,
            auth_type=ConnectorAuthType.api_key,
            base_url="http://160.119.127.193/zabbix/zabbix.php?action=dashboard.view&dashboardid=1",
            auth_config={"token": "legacy-token"},
        ),
    )

    captured = {}

    def fake_get_active_problems(self, limit=1000):
        captured["api_url"] = self.api_url
        return [
            {
                "eventid": "500",
                "name": "Cabinet disconnection",
                "hosts": [{"hostid": "900", "host": "Gwarimpa OLT"}],
                "tags": [],
            }
        ]

    monkeypatch.setattr(ZabbixConnectorClient, "get_active_problems", fake_get_active_problems)

    problems = list_live_link_down_problems(db_session)

    assert captured["api_url"] == "http://160.119.127.193/zabbix/api_jsonrpc.php"
    assert len(problems) == 1
    assert problems[0].host_name == "Gwarimpa OLT"
