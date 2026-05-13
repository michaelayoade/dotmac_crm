from __future__ import annotations

from cryptography.fernet import Fernet

from app.models.connector import ConnectorAuthType, ConnectorConfig, ConnectorType
from app.services import zabbix


def test_resolve_api_url_converts_dashboard_url():
    resolved = zabbix._resolve_api_url("http://160.119.127.193/zabbix/zabbix.php?action=dashboard.view&dashboardid=1")
    assert resolved == "http://160.119.127.193/zabbix/api_jsonrpc.php"


def test_fetch_monitoring_devices_normalizes_host_rows(db_session, monkeypatch):
    connector = ConnectorConfig(
        name="Zabbix Test",
        connector_type=ConnectorType.zabbix,
        auth_type=ConnectorAuthType.bearer,
        base_url="http://zabbix.example.com/zabbix/zabbix.php?action=dashboard.view&dashboardid=1",
        auth_config={"token": "secret-token"},
        is_active=True,
    )
    db_session.add(connector)
    db_session.commit()

    monkeypatch.setattr(
        zabbix,
        "_rpc_call",
        lambda *args, **kwargs: {
            "result": [
                {
                    "hostid": "10101",
                    "name": "DAFR-2",
                    "available": "1",
                    "snmp_available": "2",
                    "status": "0",
                }
            ]
        },
    )

    rows = zabbix.fetch_monitoring_devices(db_session)

    assert rows == [
        {
            "id": "10101",
            "title": "DAFR-2",
            "name": "DAFR-2",
            "ping_state": "up",
            "snmp_state": "down",
            "status": "0",
            "source": "zabbix",
        }
    ]


def test_configure_connector_stores_encrypted_credentials_and_token(db_session, monkeypatch):
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))
    connector = ConnectorConfig(
        name="Zabbix Existing",
        connector_type=ConnectorType.zabbix,
        auth_type=ConnectorAuthType.basic,
        base_url="http://160.119.127.193/zabbix/zabbix.php?action=dashboard.view&dashboardid=1",
        auth_config={},
        is_active=True,
    )
    db_session.add(connector)
    db_session.commit()
    db_session.refresh(connector)

    monkeypatch.setattr(zabbix, "_session_auth_token", lambda *args, **kwargs: "fresh-token")

    updated = zabbix.configure_connector(
        db_session,
        connector,
        name="Zabbix",
        base_url="http://160.119.127.193/zabbix/zabbix.php?action=dashboard.view&dashboardid=1",
        username="Admin",
        password="super-secret",
        timeout_sec=20,
        notes="Primary monitoring",
        is_active=True,
    )

    assert updated.base_url == "http://160.119.127.193/zabbix/api_jsonrpc.php"
    assert updated.auth_config["username"] == "Admin"
    assert "password_enc" in updated.auth_config
    assert "token_enc" in updated.auth_config
    assert updated.auth_config.get("password_enc") != "super-secret"
    assert updated.auth_config.get("token_enc") != "fresh-token"
    assert zabbix.ensure_api_token(db_session, updated) == "fresh-token"
