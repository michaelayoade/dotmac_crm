import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import ai as ai_api
from app.api import integrations as integrations_api
from app.api import nextcloud_talk as nextcloud_talk_api
from app.api import notifications as notifications_api
from app.api import subscribers as subscribers_api
from app.main import _ensure_storage, _start_jobs
from app.models.notification import DeliveryStatus, NotificationChannel
from app.schemas.ai_insight import AnalyzeRequest
from app.schemas.nextcloud_talk import NextcloudTalkLoginRequest
from app.schemas.notification import NotificationBulkCreateRequest, NotificationDeliveryBulkUpdateRequest
from app.schemas.subscriber import SubscriberBulkSync, SubscriberSyncData
from app.services.nextcloud_talk import NextcloudTalkError


def test_subscriber_bulk_sync_logs_summary_and_item_failures(db_session, caplog, monkeypatch):
    payload = SubscriberBulkSync(
        external_system="splynx",
        subscribers=[
            SubscriberSyncData(external_id="ok-1", service_name="Fiber"),
            SubscriberSyncData(external_id="bad-2", service_name="Wireless"),
        ],
    )

    monkeypatch.setattr(subscribers_api.subscriber_service, "get_by_external_id", lambda *args, **kwargs: None)

    def _sync_from_external(db, external_system, external_id, data):
        if external_id == "bad-2":
            raise RuntimeError("boom")
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(subscribers_api.subscriber_service, "sync_from_external", _sync_from_external)

    with caplog.at_level("INFO", logger="app.api.subscribers"):
        result = subscribers_api.sync_subscribers(payload, db=db_session)

    assert result["created"] == 1
    assert result["updated"] == 0
    assert len(result["errors"]) == 1
    assert "subscriber_bulk_sync_started external_system=splynx count=2" in caplog.text
    assert "subscriber_bulk_sync_item_failed external_system=splynx external_id=bad-2 error=boom" in caplog.text
    assert "subscriber_bulk_sync_completed external_system=splynx created=1 updated=0 errors=1" in caplog.text


def test_integration_run_logs_request_and_start(db_session, caplog, monkeypatch):
    run = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(integrations_api.integration_service.integration_jobs, "run", lambda db, job_id: run)

    with caplog.at_level("INFO", logger="app.api.integrations"):
        result = integrations_api.run_integration_job("job-123", db=db_session)

    assert result is run
    assert "integration_job_run_requested job_id=job-123" in caplog.text
    assert f"integration_job_run_started job_id=job-123 run_id={run.id}" in caplog.text


def test_nextcloud_talk_me_login_logs_success(db_session, caplog, monkeypatch):
    payload = NextcloudTalkLoginRequest(
        base_url="https://talk.example.com",
        username="alice",
        app_password="secret",
    )
    status_obj = SimpleNamespace(base_url=payload.base_url, username=payload.username)
    monkeypatch.setattr(nextcloud_talk_api, "talk_connect", lambda *args, **kwargs: status_obj)

    with caplog.at_level("INFO", logger="app.api.nextcloud_talk"):
        result = nextcloud_talk_api.me_login(payload, db=db_session, auth={"person_id": "person-1"})

    assert result["connected"] is True
    assert "nextcloud_talk_me_login_requested actor_id=person-1" in caplog.text
    assert "nextcloud_talk_me_login_completed actor_id=person-1" in caplog.text


def test_nextcloud_talk_me_login_logs_failure(db_session, caplog, monkeypatch):
    payload = NextcloudTalkLoginRequest(
        base_url="https://talk.example.com",
        username="alice",
        app_password="secret",
    )
    monkeypatch.setattr(nextcloud_talk_api, "talk_connect", lambda *args, **kwargs: (_ for _ in ()).throw(NextcloudTalkError("remote down")))

    with caplog.at_level("INFO", logger="app.api.nextcloud_talk"), pytest.raises(HTTPException) as exc_info:
        nextcloud_talk_api.me_login(payload, db=db_session, auth={"person_id": "person-1"})

    assert exc_info.value.status_code == 502
    assert "nextcloud_talk_me_login_requested actor_id=person-1" in caplog.text
    assert "nextcloud_talk_me_login_failed actor_id=person-1 error=remote down" in caplog.text


def test_ai_analysis_logs_request_and_completion(db_session, caplog, monkeypatch):
    payload = AnalyzeRequest(entity_type="ticket", entity_id="ticket-7", params={"priority": "high"})
    insight = SimpleNamespace(id=uuid.uuid4(), status=SimpleNamespace(value="completed"))
    monkeypatch.setattr(ai_api.intelligence_engine, "invoke", lambda *args, **kwargs: insight)

    with caplog.at_level("INFO", logger="app.api.ai"):
        result = ai_api.invoke_analysis("ticket_analyst", payload, db=db_session, auth={"person_id": "user-1"})

    assert result["insight_id"] == str(insight.id)
    assert (
        "ai_analysis_requested persona_key=ticket_analyst entity_type=ticket entity_id=ticket-7 actor_id=user-1"
        in caplog.text
    )
    assert (
        f"ai_analysis_completed persona_key=ticket_analyst entity_type=ticket entity_id=ticket-7 "
        f"actor_id=user-1 insight_id={insight.id}" in caplog.text
    )


def test_ai_analysis_logs_failure(db_session, caplog, monkeypatch):
    payload = AnalyzeRequest(entity_type="ticket", entity_id="ticket-7", params={"priority": "high"})
    monkeypatch.setattr(
        ai_api.intelligence_engine,
        "invoke",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("engine unavailable")),
    )

    with caplog.at_level("INFO", logger="app.api.ai"), pytest.raises(HTTPException) as exc_info:
        ai_api.invoke_analysis("ticket_analyst", payload, db=db_session, auth={"person_id": "user-1"})

    assert exc_info.value.status_code == 400
    assert (
        "ai_analysis_requested persona_key=ticket_analyst entity_type=ticket entity_id=ticket-7 actor_id=user-1"
        in caplog.text
    )
    assert (
        "ai_analysis_failed persona_key=ticket_analyst entity_type=ticket entity_id=ticket-7 "
        "actor_id=user-1 error=engine unavailable" in caplog.text
    )


def test_ai_analysis_async_logs_queue_failure(caplog, monkeypatch):
    payload = AnalyzeRequest(entity_type="ticket", entity_id="ticket-9", params={})
    monkeypatch.setattr(ai_api.persona_registry, "get", lambda persona_key: SimpleNamespace(key=persona_key))

    class _FailingTask:
        @staticmethod
        def delay(*args, **kwargs):
            raise RuntimeError("queue offline")

    monkeypatch.setattr("app.tasks.intelligence.invoke_persona_async", _FailingTask)

    with caplog.at_level("INFO", logger="app.api.ai"), pytest.raises(HTTPException) as exc_info:
        ai_api.invoke_analysis_async("ticket_analyst", payload, auth={"person_id": "user-2"})

    assert exc_info.value.status_code == 503
    assert (
        "ai_analysis_async_requested persona_key=ticket_analyst entity_type=ticket entity_id=ticket-9 actor_id=user-2"
        in caplog.text
    )
    assert (
        "ai_analysis_async_queue_unavailable persona_key=ticket_analyst entity_type=ticket entity_id=ticket-9 "
        "actor_id=user-2 error=queue offline" in caplog.text
    )


def test_notification_bulk_create_logs_summary(db_session, caplog, monkeypatch):
    payload = NotificationBulkCreateRequest(
        channel=NotificationChannel.email,
        recipients=["a@example.com", "b@example.com"],
        subject="Subject",
        body="Body",
    )
    monkeypatch.setattr(
        notifications_api.notification_service.notifications,
        "bulk_create_response",
        lambda db, req: {"created": 2, "notification_ids": [uuid.uuid4(), uuid.uuid4()]},
    )

    with caplog.at_level("INFO", logger="app.api.notifications"):
        result = notifications_api.create_notifications_bulk(payload, db=db_session)

    assert result.created == 2
    assert "notification_bulk_create_requested count=2" in caplog.text
    assert "notification_bulk_create_completed created=2 errors=0" in caplog.text


def test_notification_delivery_bulk_update_logs_summary(db_session, caplog, monkeypatch):
    delivery_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    payload = NotificationDeliveryBulkUpdateRequest(
        delivery_ids=delivery_ids,
        status=DeliveryStatus.delivered,
    )
    monkeypatch.setattr(
        notifications_api.notification_service.deliveries,
        "bulk_update_response",
        lambda db, req: {"updated": 3},
    )

    with caplog.at_level("INFO", logger="app.api.notifications"):
        result = notifications_api.update_notification_deliveries_bulk(payload, db=db_session)

    assert result.updated == 3
    assert "notification_delivery_bulk_update_requested count=3" in caplog.text
    assert "notification_delivery_bulk_update_completed updated=3 errors=0" in caplog.text


def test_startup_logs_seed_and_storage_breadcrumbs(caplog, monkeypatch):
    class _DummySession:
        def close(self):
            return None

    class _DummyStorage:
        def ensure_bucket(self):
            return None

    monkeypatch.setattr("app.main.SessionLocal", lambda: _DummySession())
    for name in (
        "seed_auth_settings",
        "seed_auth_policy_settings",
        "seed_audit_settings",
        "seed_gis_settings",
        "seed_notification_settings",
        "seed_geocoding_settings",
        "seed_scheduler_settings",
        "seed_provisioning_settings",
        "seed_projects_settings",
        "seed_workflow_settings",
        "seed_network_policy_settings",
        "seed_network_settings",
        "seed_inventory_settings",
        "seed_comms_settings",
        "seed_integration_settings",
        "seed_performance_settings",
        "seed_sla_defaults",
        "seed_bootstrap_admin_user",
    ):
        monkeypatch.setattr(f"app.main.{name}", lambda db: None)
    monkeypatch.setattr("app.main.smtp_inbound_service.start_smtp_inbound_server", lambda: None)
    monkeypatch.setattr("app.services.storage.storage", _DummyStorage())

    with caplog.at_level("INFO", logger="app.main"):
        _start_jobs()
        _ensure_storage()

    assert "app_startup_seed_begin" in caplog.text
    assert "app_startup_seed_completed" in caplog.text
    assert "app_startup_smtp_begin" in caplog.text
    assert "app_startup_smtp_completed" in caplog.text
    assert "app_startup_storage_begin" in caplog.text
    assert "app_startup_storage_completed" in caplog.text
