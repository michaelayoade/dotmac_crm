from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from app.models.connector import ConnectorAuthType, ConnectorConfig, ConnectorType
from app.models.crm.conversation import Conversation
from app.models.crm.enums import ConversationPriority, ConversationStatus, MessageStatus
from app.models.domain_settings import SettingValueType
from app.models.integration import IntegrationTarget, IntegrationTargetType
from app.models.person import ChannelType as PersonChannelType
from app.models.person import PersonChannel
from app.models.subscriber import Subscriber, SubscriberStatus
from app.models.subscriber_outreach import SubscriberOfflineOutreachLog, SubscriberStationMapping
from app.schemas.settings import DomainSettingUpdate
from app.services import subscriber_offline_outreach as service
from app.services.domain_settings import notification_settings
from app.services.splynx import customer_base_station


def _set_notification_setting(db_session, key: str, value_text: str, value_type: SettingValueType) -> None:
    payload = DomainSettingUpdate(
        value_type=value_type,
        value_text=value_text,
        value_json=value_text.lower() in {"1", "true", "yes", "on"} if value_type == SettingValueType.boolean else None,
        is_active=True,
    )
    notification_settings.upsert_by_key(db_session, key, payload)


def _create_subscriber(db_session, person, *, external_id: str, subscriber_number: str) -> Subscriber:
    person.phone = "08030000000"
    db_session.add(person)
    db_session.commit()
    subscriber = Subscriber(
        person_id=person.id,
        external_id=external_id,
        external_system="splynx",
        subscriber_number=subscriber_number,
        status=SubscriberStatus.active,
        is_active=True,
    )
    db_session.add(subscriber)
    db_session.commit()
    db_session.refresh(subscriber)
    return subscriber


def _create_whatsapp_target(db_session) -> IntegrationTarget:
    config = ConnectorConfig(
        name=f"WhatsApp Test {uuid.uuid4().hex[:8]}",
        connector_type=ConnectorType.whatsapp,
        auth_type=ConnectorAuthType.bearer,
        auth_config={"token": "test-token"},
        metadata_={"phone_number_id": "12345"},
        is_active=True,
    )
    db_session.add(config)
    db_session.commit()
    db_session.refresh(config)

    target = IntegrationTarget(
        name="WhatsApp Inbox",
        target_type=IntegrationTargetType.crm,
        connector_config_id=config.id,
        is_active=True,
    )
    db_session.add(target)
    db_session.commit()
    db_session.refresh(target)
    return target


def _configure_outreach(db_session, *, target_id: str) -> None:
    _set_notification_setting(
        db_session,
        "subscriber_offline_outreach_enabled",
        "true",
        SettingValueType.boolean,
    )
    _set_notification_setting(
        db_session,
        "subscriber_offline_outreach_local_time",
        "10:00",
        SettingValueType.string,
    )
    _set_notification_setting(
        db_session,
        "subscriber_offline_outreach_timezone",
        "Africa/Lagos",
        SettingValueType.string,
    )
    _set_notification_setting(
        db_session,
        "subscriber_offline_outreach_channel",
        "whatsapp",
        SettingValueType.string,
    )
    _set_notification_setting(
        db_session,
        "subscriber_offline_outreach_channel_target_id",
        target_id,
        SettingValueType.string,
    )
    _set_notification_setting(
        db_session,
        "subscriber_offline_outreach_cooldown_hours",
        "72",
        SettingValueType.integer,
    )
    _set_notification_setting(
        db_session,
        "subscriber_offline_outreach_message_template",
        "Hello {first_name}, we noticed your service is offline.",
        SettingValueType.string,
    )


def _set_outreach_template_payload(db_session, payload: dict) -> None:
    notification_settings.upsert_by_key(
        db_session,
        "subscriber_offline_outreach_whatsapp_template_payload",
        DomainSettingUpdate(
            value_type=SettingValueType.json,
            value_json=payload,
            is_active=True,
        ),
    )


def test_resolve_monitoring_match_maps_dafr2_label(db_session):
    monitoring_rows = [{"id": 9, "title": "DAFR-2", "ping_state": "up", "snmp_state": "up"}]
    by_normalized, by_code = service._build_monitoring_indexes(monitoring_rows)

    match = service._resolve_monitoring_match(
        db_session,
        base_station_label="ASOKORO (D-AFR2)",
        monitoring_rows=monitoring_rows,
        by_normalized=by_normalized,
        by_code=by_code,
    )

    assert match is not None
    assert match.title == "DAFR-2"
    assert match.station_status == "up"

    mapping = db_session.query(SubscriberStationMapping).filter_by(raw_customer_base_station="ASOKORO (D-AFR2)").one()
    assert mapping.monitoring_title == "DAFR-2"
    assert mapping.match_method == "station_code"


def test_resolve_monitoring_match_maps_olt_label_to_olt_host(db_session):
    monitoring_rows = [
        {"id": 1, "title": "Gudu Access", "ping_state": "up", "snmp_state": "up"},
        {"id": 2, "title": "GPON-GUDU-1", "ping_state": "up", "snmp_state": "up"},
        {"id": 3, "title": "Gudu Huawei OLT", "ping_state": "up", "snmp_state": "up"},
    ]
    by_normalized, by_code = service._build_monitoring_indexes(monitoring_rows)

    match = service._resolve_monitoring_match(
        db_session,
        base_station_label="Gudu OLT 1 (Port 3)",
        monitoring_rows=monitoring_rows,
        by_normalized=by_normalized,
        by_code=by_code,
    )

    assert match is not None
    assert match.title == "Gudu Huawei OLT"
    assert match.match_method == "site_family_olt"


def test_resolve_monitoring_match_maps_dloko_alias_to_lokogoma(db_session):
    monitoring_rows = [
        {"id": 1, "title": "Lokogoma Access", "ping_state": "up", "snmp_state": "up"},
        {"id": 2, "title": "Lokogoma AP-1", "ping_state": "up", "snmp_state": "up"},
    ]
    by_normalized, by_code = service._build_monitoring_indexes(monitoring_rows)

    match = service._resolve_monitoring_match(
        db_session,
        base_station_label="DLOKO-4",
        monitoring_rows=monitoring_rows,
        by_normalized=by_normalized,
        by_code=by_code,
    )

    assert match is not None
    assert match.title == "Lokogoma Access"


def test_resolve_monitoring_match_maps_gwarinpa_alias_to_gwarimpa(db_session):
    monitoring_rows = [
        {"id": 1, "title": "Gwarimpa Huawei OLT", "ping_state": "up", "snmp_state": "up"},
        {"id": 2, "title": "Gwarimpa Access", "ping_state": "up", "snmp_state": "up"},
    ]
    by_normalized, by_code = service._build_monitoring_indexes(monitoring_rows)

    match = service._resolve_monitoring_match(
        db_session,
        base_station_label="gwarinpa olt port 12",
        monitoring_rows=monitoring_rows,
        by_normalized=by_normalized,
        by_code=by_code,
    )

    assert match is not None
    assert match.title == "Gwarimpa Huawei OLT"


def test_resolve_monitoring_match_maps_dmpape_alias(db_session):
    monitoring_rows = [
        {"id": 1, "title": "Mpape Switch", "ping_state": "up", "snmp_state": "up"},
        {"id": 2, "title": "DMPAPE-1", "ping_state": "up", "snmp_state": "up"},
    ]
    by_normalized, by_code = service._build_monitoring_indexes(monitoring_rows)

    match = service._resolve_monitoring_match(
        db_session,
        base_station_label="DMPAPE-1",
        monitoring_rows=monitoring_rows,
        by_normalized=by_normalized,
        by_code=by_code,
    )

    assert match is not None
    assert match.title == "DMPAPE-1"


def test_resolve_monitoring_match_maps_karasana_alias_to_karsana_olt(db_session):
    monitoring_rows = [
        {"id": 1, "title": "Karsana Access", "ping_state": "up", "snmp_state": "up"},
        {"id": 2, "title": "Karsana Huawei OLT", "ping_state": "up", "snmp_state": "up"},
    ]
    by_normalized, by_code = service._build_monitoring_indexes(monitoring_rows)

    match = service._resolve_monitoring_match(
        db_session,
        base_station_label="Karasana OLT 1 (Port 1)",
        monitoring_rows=monitoring_rows,
        by_normalized=by_normalized,
        by_code=by_code,
    )

    assert match is not None
    assert match.title == "Karsana Huawei OLT"
    assert match.match_method == "site_family_olt"


def test_resolve_monitoring_match_maps_dlifecamp_alias(db_session):
    monitoring_rows = [
        {"id": 1, "title": "DLIFECAMP AP-1", "ping_state": "up", "snmp_state": "up"},
    ]
    by_normalized, by_code = service._build_monitoring_indexes(monitoring_rows)

    match = service._resolve_monitoring_match(
        db_session,
        base_station_label="DLIFECAMP-1",
        monitoring_rows=monitoring_rows,
        by_normalized=by_normalized,
        by_code=by_code,
    )

    assert match is not None
    assert match.title == "DLIFECAMP AP-1"
    assert match.match_method == "exact_title_alias"


def test_customer_base_station_reads_additional_attributes():
    customer = {
        "id": "12896",
        "login": "100012896",
        "additional_attributes": {
            "base_station": "ASOKORO (D-AFR2)",
        },
    }

    assert customer_base_station(customer) == "ASOKORO (D-AFR2)"


def test_build_whatsapp_template_components_from_body_and_header(db_session, person):
    subscriber = _create_subscriber(db_session, person, external_id="12896", subscriber_number="SUB-12896")
    template_payload = {
        "name": "offline_outreach",
        "language": "en",
        "body": "Hello {{1}}, subscriber {{2}} on {{3}}",
        "components": [
            {"type": "HEADER", "format": "TEXT", "text": "Alert for {{1}}"},
            {"type": "BODY", "text": "Hello {{1}}, subscriber {{2}} on {{3}}"},
        ],
    }

    components = service._build_whatsapp_template_components(
        template_payload,
        person=person,
        subscriber=subscriber,
        base_station_label="ASOKORO (D-AFR2)",
    )

    assert components == [
        {"type": "header", "parameters": [{"type": "text", "text": person.first_name}]},
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": person.first_name},
                {"type": "text", "text": "SUB-12896"},
                {"type": "text", "text": "ASOKORO (D-AFR2)"},
            ],
        },
    ]


def test_build_whatsapp_template_components_uses_saved_parameter_values(db_session, person):
    subscriber = _create_subscriber(db_session, person, external_id="12896", subscriber_number="SUB-12896")
    template_payload = {
        "name": "offline_outreach",
        "language": "en",
        "body": "Hello {{1}}, case {{2}}",
        "components": [
            {"type": "BODY", "text": "Hello {{1}}, case {{2}}"},
        ],
        "parameter_values": {
            "1": "customer",
            "2": "{subscriber_number}",
        },
    }

    components = service._build_whatsapp_template_components(
        template_payload,
        person=person,
        subscriber=subscriber,
        base_station_label="ASOKORO (D-AFR2)",
    )

    assert components == [
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": "customer"},
                {"type": "text", "text": "SUB-12896"},
            ],
        }
    ]


def test_build_whatsapp_template_components_supports_named_parameters(db_session, person):
    subscriber = _create_subscriber(db_session, person, external_id="12896", subscriber_number="SUB-12896")
    template_payload = {
        "name": "offline_outreach",
        "language": "en",
        "body": "Hello {{first_name}}, {{text}}",
        "components": [
            {"type": "BODY", "text": "Hello {{first_name}}, {{text}}"},
        ],
        "parameter_values": {
            "text": "we noticed your service at {base_station} is offline",
        },
    }

    components = service._build_whatsapp_template_components(
        template_payload,
        person=person,
        subscriber=subscriber,
        base_station_label="ASOKORO (D-AFR2)",
    )

    assert components == [
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": person.first_name, "parameter_name": "first_name"},
                {
                    "type": "text",
                    "text": "we noticed your service at ASOKORO (D-AFR2) is offline",
                    "parameter_name": "text",
                },
            ],
        }
    ]


def test_run_daily_offline_outreach_sends_only_without_open_conversation(db_session, person, monkeypatch):
    subscriber = _create_subscriber(db_session, person, external_id="12896", subscriber_number="SUB-12896")
    target = _create_whatsapp_target(db_session)
    _configure_outreach(db_session, target_id=str(target.id))
    _set_outreach_template_payload(
        db_session,
        {
            "name": "offline_outreach",
            "language": "en",
            "body": "Hello {{1}}, subscriber {{2}} on {{3}}",
            "components": [
                {"type": "BODY", "text": "Hello {{1}}, subscriber {{2}} on {{3}}"},
            ],
        },
    )

    person_channel = PersonChannel(
        person_id=person.id,
        channel_type=PersonChannelType.whatsapp,
        address="+2348030000000",
        is_primary=True,
    )
    db_session.add(person_channel)
    db_session.commit()

    monkeypatch.setattr(
        service.subscriber_reports,
        "online_customers_last_24h_rows",
        lambda *args, **kwargs: [{"subscriber_id": str(subscriber.id)}],
    )
    monkeypatch.setattr(
        service.splynx,
        "fetch_customers",
        lambda _db: [
            {
                "id": "12896",
                "login": "SUB-12896",
                "name": "Test User",
                "additional_attributes": {"base_station": "ASOKORO (D-AFR2)"},
            }
        ],
    )
    monkeypatch.setattr(
        service.splynx,
        "fetch_monitoring_devices",
        lambda _db: [{"id": "9", "title": "DAFR-2", "ping_state": "up", "snmp_state": "up"}],
    )
    monkeypatch.setattr(
        service.inbox_service,
        "send_message",
        lambda *args, **kwargs: SimpleNamespace(id=None, status=MessageStatus.sent),
    )

    result = service.run_daily_offline_outreach(
        db_session,
        now_utc=datetime(2026, 5, 11, 9, 30, tzinfo=UTC),
    )

    assert result["status"] == "success"
    assert result["sent"] == 1
    assert db_session.query(Conversation).count() == 1

    log = db_session.query(SubscriberOfflineOutreachLog).one()
    assert log.decision_status == "sent"
    assert log.decision_reason is None


def test_run_daily_offline_outreach_uses_selected_whatsapp_template(db_session, person, monkeypatch):
    subscriber = _create_subscriber(db_session, person, external_id="12896", subscriber_number="SUB-12896")
    target = _create_whatsapp_target(db_session)
    _configure_outreach(db_session, target_id=str(target.id))
    _set_outreach_template_payload(
        db_session,
        {
            "name": "offline_outreach",
            "language": "en",
            "body": "Hello {{1}}, subscriber {{2}} on {{3}}",
            "components": [
                {"type": "BODY", "text": "Hello {{1}}, subscriber {{2}} on {{3}}"},
            ],
        },
    )

    person_channel = PersonChannel(
        person_id=person.id,
        channel_type=PersonChannelType.whatsapp,
        address="+2348030000000",
        is_primary=True,
    )
    db_session.add(person_channel)
    db_session.commit()

    monkeypatch.setattr(
        service.subscriber_reports,
        "online_customers_last_24h_rows",
        lambda *args, **kwargs: [{"subscriber_id": str(subscriber.id)}],
    )
    monkeypatch.setattr(
        service.splynx,
        "fetch_customers",
        lambda _db: [
            {
                "id": "12896",
                "login": "SUB-12896",
                "name": "Test User",
                "additional_attributes": {"base_station": "ASOKORO (D-AFR2)"},
            }
        ],
    )
    monkeypatch.setattr(
        service.splynx,
        "fetch_monitoring_devices",
        lambda _db: [{"id": "9", "title": "DAFR-2", "ping_state": "up", "snmp_state": "up"}],
    )

    captured = {}

    def _capture_send(_db, payload, **kwargs):
        captured["body"] = payload.body
        captured["template_name"] = payload.whatsapp_template_name
        captured["template_language"] = payload.whatsapp_template_language
        captured["template_components"] = payload.whatsapp_template_components
        return SimpleNamespace(id=None, status=MessageStatus.sent)

    monkeypatch.setattr(service.inbox_service, "send_message", _capture_send)

    result = service.run_daily_offline_outreach(
        db_session,
        now_utc=datetime(2026, 5, 11, 9, 30, tzinfo=UTC),
    )

    assert result["status"] == "success"
    assert result["sent"] == 1
    assert captured["body"] == f"Hello {person.first_name}, subscriber SUB-12896 on ASOKORO (D-AFR2)"
    assert captured["template_name"] == "offline_outreach"
    assert captured["template_language"] == "en"
    assert captured["template_components"] == [
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": person.first_name},
                {"type": "text", "text": "SUB-12896"},
                {"type": "text", "text": "ASOKORO (D-AFR2)"},
            ],
        }
    ]


def test_run_daily_offline_outreach_skips_when_whatsapp_template_not_configured(db_session, person, monkeypatch):
    subscriber = _create_subscriber(db_session, person, external_id="12896", subscriber_number="SUB-12896")
    target = _create_whatsapp_target(db_session)
    _configure_outreach(db_session, target_id=str(target.id))

    person_channel = PersonChannel(
        person_id=person.id,
        channel_type=PersonChannelType.whatsapp,
        address="+2348030000000",
        is_primary=True,
    )
    db_session.add(person_channel)
    db_session.commit()

    monkeypatch.setattr(
        service.subscriber_reports,
        "online_customers_last_24h_rows",
        lambda *args, **kwargs: [{"subscriber_id": str(subscriber.id)}],
    )
    monkeypatch.setattr(
        service.splynx,
        "fetch_customers",
        lambda _db: [
            {
                "id": "12896",
                "login": "SUB-12896",
                "name": "Test User",
                "additional_attributes": {"base_station": "ASOKORO (D-AFR2)"},
            }
        ],
    )
    monkeypatch.setattr(
        service.splynx,
        "fetch_monitoring_devices",
        lambda _db: [{"id": "9", "title": "DAFR-2", "ping_state": "up", "snmp_state": "up"}],
    )

    def _unexpected_send(*args, **kwargs):
        raise AssertionError("send_message should not be called without an approved WhatsApp template")

    monkeypatch.setattr(service.inbox_service, "send_message", _unexpected_send)

    result = service.run_daily_offline_outreach(
        db_session,
        now_utc=datetime(2026, 5, 11, 9, 30, tzinfo=UTC),
    )

    assert result["status"] == "success"
    assert result["sent"] == 0
    assert result["skipped"] == 1

    log = db_session.query(SubscriberOfflineOutreachLog).one()
    assert log.decision_status == "skipped"
    assert log.decision_reason == "missing_whatsapp_template"


def test_enrich_rows_with_station_status_populates_report_fields(db_session, person, monkeypatch):
    subscriber = _create_subscriber(db_session, person, external_id="12896", subscriber_number="SUB-12896")

    monkeypatch.setattr(
        service.splynx,
        "fetch_customers",
        lambda _db: [
            {
                "id": "12896",
                "login": "SUB-12896",
                "name": "Test User",
                "additional_attributes": {"base_station": "ASOKORO (D-AFR2)"},
            }
        ],
    )
    monkeypatch.setattr(
        service.splynx,
        "fetch_monitoring_devices",
        lambda _db: [{"id": "9", "title": "DAFR-2", "ping_state": "up", "snmp_state": "up"}],
    )

    rows = service.enrich_rows_with_station_status(
        db_session,
        [
            {
                "subscriber_id": str(subscriber.id),
                "subscriber_number": "SUB-12896",
                "splynx_customer_id": "12896",
                "splynx_login": "SUB-12896",
                "base_station": "ASOKORO (D-AFR2)",
            }
        ],
    )

    assert rows[0]["base_station"] == "ASOKORO (D-AFR2)"
    assert rows[0]["station_status"] == "up"
    assert rows[0]["station_monitoring_title"] == "DAFR-2"


def test_run_daily_offline_outreach_skips_when_open_conversation_exists(db_session, person, monkeypatch):
    subscriber = _create_subscriber(db_session, person, external_id="12896", subscriber_number="SUB-12896")
    target = _create_whatsapp_target(db_session)
    _configure_outreach(db_session, target_id=str(target.id))

    existing_conversation = Conversation(
        person_id=person.id,
        status=ConversationStatus.open,
        priority=ConversationPriority.none,
        is_active=True,
        is_muted=False,
    )
    db_session.add(existing_conversation)
    db_session.commit()

    monkeypatch.setattr(
        service.subscriber_reports,
        "online_customers_last_24h_rows",
        lambda *args, **kwargs: [{"subscriber_id": str(subscriber.id)}],
    )
    monkeypatch.setattr(
        service.splynx,
        "fetch_customers",
        lambda _db: [
            {
                "id": "12896",
                "login": "SUB-12896",
                "name": "Test User",
                "additional_attributes": {"base_station": "ASOKORO (D-AFR2)"},
            }
        ],
    )
    monkeypatch.setattr(
        service.splynx,
        "fetch_monitoring_devices",
        lambda _db: [{"id": "9", "title": "DAFR-2", "ping_state": "up", "snmp_state": "up"}],
    )

    def _unexpected_send(*args, **kwargs):
        raise AssertionError("send_message should not be called when an open conversation exists")

    monkeypatch.setattr(service.inbox_service, "send_message", _unexpected_send)

    result = service.run_daily_offline_outreach(
        db_session,
        now_utc=datetime(2026, 5, 11, 9, 30, tzinfo=UTC),
    )

    assert result["status"] == "success"
    assert result["sent"] == 0
    assert result["skipped"] == 1

    log = db_session.query(SubscriberOfflineOutreachLog).one()
    assert log.decision_status == "skipped"
    assert log.decision_reason == "open_conversation"


def test_run_daily_offline_outreach_resolves_created_conversation_when_send_fails(db_session, person, monkeypatch):
    subscriber = _create_subscriber(db_session, person, external_id="12896", subscriber_number="SUB-12896")
    target = _create_whatsapp_target(db_session)
    _configure_outreach(db_session, target_id=str(target.id))
    _set_outreach_template_payload(
        db_session,
        {
            "name": "offline_outreach",
            "language": "en",
            "body": "Hello {{1}}, subscriber {{2}} on {{3}}",
            "components": [
                {"type": "BODY", "text": "Hello {{1}}, subscriber {{2}} on {{3}}"},
            ],
        },
    )

    person_channel = PersonChannel(
        person_id=person.id,
        channel_type=PersonChannelType.whatsapp,
        address="+2348030000000",
        is_primary=True,
    )
    db_session.add(person_channel)
    db_session.commit()

    monkeypatch.setattr(
        service.subscriber_reports,
        "online_customers_last_24h_rows",
        lambda *args, **kwargs: [{"subscriber_id": str(subscriber.id)}],
    )
    monkeypatch.setattr(
        service.splynx,
        "fetch_customers",
        lambda _db: [
            {
                "id": "12896",
                "login": "SUB-12896",
                "name": "Test User",
                "additional_attributes": {"base_station": "ASOKORO (D-AFR2)"},
            }
        ],
    )
    monkeypatch.setattr(
        service.splynx,
        "fetch_monitoring_devices",
        lambda _db: [{"id": "9", "title": "DAFR-2", "ping_state": "up", "snmp_state": "up"}],
    )
    monkeypatch.setattr(
        service.inbox_service,
        "send_message",
        lambda *args, **kwargs: SimpleNamespace(id=None, status=MessageStatus.failed),
    )

    result = service.run_daily_offline_outreach(
        db_session,
        now_utc=datetime(2026, 5, 11, 9, 30, tzinfo=UTC),
    )

    assert result["status"] == "success"
    assert result["failed"] == 1

    conversation = db_session.query(Conversation).one()
    assert conversation.status == ConversationStatus.resolved
    assert conversation.resolved_at is not None
    assert dict(conversation.metadata_ or {}).get("outreach_failure_reason") == "send_failed"

    log = db_session.query(SubscriberOfflineOutreachLog).one()
    assert log.decision_status == "failed"
    assert log.decision_reason == "send_failed"
