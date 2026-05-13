from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection, MessageStatus
from app.models.person import Person
from app.services.crm import ai_intake_runtime


def _make_person(db_session):
    person = Person(email=f"runtime-{uuid.uuid4().hex[:8]}@example.com", first_name="Runtime", last_name="Audit")
    db_session.add(person)
    db_session.flush()
    return person


def test_provider_connection_pool_state_reports_per_request_client(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.crm.ai_intake_runtime.ai_gateway.get_endpoint_config",
        lambda db, endpoint: SimpleNamespace(
            label=endpoint,
            base_url=f"https://{endpoint}.example.test",
            model=f"{endpoint}-model",
            timeout_seconds=30.0,
            max_retries=2,
        ),
    )

    snapshot = ai_intake_runtime.ai_provider_connection_pool_state(db_session)

    assert snapshot["client_mode"] == "per_request_httpx_client"
    assert snapshot["pool_reuse_enabled"] is False
    assert snapshot["endpoints"][0]["host"] == "primary.example.test"


def test_ai_intake_runtime_audit_reports_recent_ai_error_metadata(db_session, monkeypatch):
    person = _make_person(db_session)
    escalated = Conversation(person_id=person.id, status=ConversationStatus.open, is_active=True)
    resolved = Conversation(person_id=person.id, status=ConversationStatus.open, is_active=True)
    db_session.add_all([escalated, resolved])
    db_session.flush()

    now = datetime.now(UTC)
    escalated.metadata_ = {
        "ai_intake": {
            "status": "escalated",
            "escalated_reason": "ai_error",
            "failure_type": "timeout",
            "timeout_type": "read",
            "provider": "primary",
            "endpoint": "primary",
            "request_id": "req-timeout",
            "updated_at": now.isoformat(),
        }
    }
    resolved.metadata_ = {
        "ai_intake": {
            "status": "resolved",
            "resolved_at": (now - timedelta(minutes=5)).isoformat(),
            "updated_at": (now - timedelta(minutes=5)).isoformat(),
        }
    }
    db_session.add_all(
        [
            Message(
                conversation_id=escalated.id,
                channel_type=ChannelType.whatsapp,
                direction=MessageDirection.inbound,
                status=MessageStatus.received,
                body="Need help",
                received_at=now,
            ),
            Message(
                conversation_id=resolved.id,
                channel_type=ChannelType.whatsapp,
                direction=MessageDirection.inbound,
                status=MessageStatus.received,
                body="Resolved earlier",
                received_at=now - timedelta(minutes=5),
            ),
        ]
    )
    db_session.commit()

    monkeypatch.setattr(
        "app.services.crm.ai_intake_runtime.ai_circuit_state_snapshot",
        lambda db: {"captured_at": now.isoformat(), "endpoints": [], "any_open": False},
    )
    monkeypatch.setattr(
        "app.services.crm.ai_intake_runtime.ai_worker_health_snapshot",
        lambda timeout=1.5: {
            "captured_at": now.isoformat(),
            "worker_count": 1,
            "workers": [],
            "queue_names": ["celery"],
        },
    )
    monkeypatch.setattr(
        "app.services.crm.ai_intake_runtime.ai_queue_depth_snapshot",
        lambda queue_names=None: {"captured_at": now.isoformat(), "available": True, "queues": [], "total_depth": 0},
    )
    monkeypatch.setattr(
        "app.services.crm.ai_intake_runtime.ai_provider_connection_pool_state",
        lambda db: {"client_mode": "per_request_httpx_client"},
    )

    audit = ai_intake_runtime.ai_intake_runtime_audit(db_session)

    assert audit["classification_candidates"]["provider_timeout"] is True
    assert audit["intake"]["recent_ai_error_count"] == 1
    assert audit["intake"]["recent_ai_errors"][0]["failure_type"] == "timeout"
    assert audit["intake"]["last_resolved_age_seconds"] is not None


def test_ai_queue_depth_snapshot_handles_missing_redis(monkeypatch):
    monkeypatch.setattr("app.services.crm.ai_intake_runtime._redis_client", lambda: None)

    snapshot = ai_intake_runtime.ai_queue_depth_snapshot(["celery"])

    assert snapshot["available"] is False
    assert snapshot["reason"] == "redis_broker_unavailable"
