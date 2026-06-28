from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from app.models.infrastructure import (
    InfrastructureAlert,
    InfrastructureAlertCategory,
    InfrastructureAlertSeverity,
    InfrastructureAlertStatus,
)
from app.models.notification import Notification, NotificationChannel
from app.models.rbac import Permission, PersonPermission
from app.services import infrastructure_health


def _result(
    *,
    severity=InfrastructureAlertSeverity.warning,
    status="degraded",
    summary="Redis latency is high.",
):
    return infrastructure_health.HealthCheckResult(
        category=InfrastructureAlertCategory.cache,
        component="redis",
        check_key="redis_connectivity",
        status=status,
        severity=severity,
        summary=summary,
        last_activity_at=datetime.now(UTC),
        metadata={"latency_ms": 1200},
    )


def _grant_monitoring(db, person):
    permission = Permission(
        key=infrastructure_health.MONITORING_READ_PERMISSION,
        description="View infrastructure health and alerts.",
        is_active=True,
    )
    db.add(permission)
    db.flush()
    db.add(PersonPermission(person_id=person.id, permission_id=permission.id))
    db.commit()
    return permission


def test_alert_creation_deduplicates_by_fingerprint(db_session, person):
    _grant_monitoring(db_session, person)

    first = infrastructure_health.upsert_alerts_from_results(db_session, [_result()])
    second = infrastructure_health.upsert_alerts_from_results(db_session, [_result(summary="Redis still slow.")])

    alerts = db_session.query(InfrastructureAlert).all()
    notifications = db_session.query(Notification).filter(Notification.channel == NotificationChannel.push).all()

    assert first["created"] == 1
    assert second["created"] == 0
    assert len(alerts) == 1
    assert alerts[0].occurrence_count == 2
    assert alerts[0].summary == "Redis still slow."
    assert len(notifications) == 1


def test_alert_resolves_when_check_becomes_healthy(db_session, person):
    _grant_monitoring(db_session, person)
    infrastructure_health.upsert_alerts_from_results(db_session, [_result()])

    resolved = infrastructure_health.upsert_alerts_from_results(
        db_session,
        [
            _result(
                severity=InfrastructureAlertSeverity.info,
                status="healthy",
                summary="Redis/cache accepted a ping.",
            )
        ],
    )

    alert = db_session.query(InfrastructureAlert).one()
    assert resolved["resolved"] == 1
    assert alert.status == InfrastructureAlertStatus.resolved
    assert alert.resolved_at is not None


def test_alert_reopen_and_escalation_notify_monitoring_users(db_session, person):
    _grant_monitoring(db_session, person)
    infrastructure_health.upsert_alerts_from_results(db_session, [_result()])
    infrastructure_health.upsert_alerts_from_results(
        db_session,
        [_result(severity=InfrastructureAlertSeverity.info, status="healthy", summary="Redis healthy.")],
    )

    reopened = infrastructure_health.upsert_alerts_from_results(
        db_session,
        [_result(severity=InfrastructureAlertSeverity.critical, status="unhealthy", summary="Redis is down.")],
    )

    alert = db_session.query(InfrastructureAlert).one()
    notifications = db_session.query(Notification).order_by(Notification.created_at.asc()).all()

    assert reopened["reopened"] == 1
    assert reopened["escalated"] == 1
    assert alert.status == InfrastructureAlertStatus.open
    assert alert.severity == InfrastructureAlertSeverity.critical
    assert len(notifications) == 2
    assert notifications[-1].recipient == str(person.id)
    assert "Open: /admin/system/health/alerts?status=open" in (notifications[-1].body or "")


def test_route_access_requires_monitoring_permission(db_session, person):
    from app.services.auth_dependencies import require_permission

    guard = require_permission(infrastructure_health.MONITORING_READ_PERMISSION)
    with pytest.raises(HTTPException) as exc_info:
        guard(auth={"person_id": str(person.id), "roles": [], "scopes": []}, db=db_session)

    assert exc_info.value.status_code == 403


def test_admin_role_can_access_monitoring_permission(db_session, person):
    from app.services.auth_dependencies import require_permission

    guard = require_permission(infrastructure_health.MONITORING_READ_PERMISSION)
    auth = {"person_id": str(person.id), "roles": ["admin"], "scopes": []}

    assert guard(auth=auth, db=db_session) == auth
