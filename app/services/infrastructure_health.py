from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.infrastructure import (
    InfrastructureAlert,
    InfrastructureAlertCategory,
    InfrastructureAlertSeverity,
    InfrastructureAlertStatus,
)
from app.models.notification import Notification, NotificationChannel, NotificationStatus
from app.models.person import Person
from app.models.rbac import Permission, PersonPermission, PersonRole, Role, RolePermission
from app.models.scheduler import ScheduledTask

MONITORING_READ_PERMISSION = "system:monitoring:read"
MONITORING_WRITE_PERMISSION = "system:monitoring:write"


_SEVERITY_RANK = {
    InfrastructureAlertSeverity.info: 0,
    InfrastructureAlertSeverity.warning: 1,
    InfrastructureAlertSeverity.critical: 2,
}


@dataclass(frozen=True)
class HealthCheckResult:
    category: InfrastructureAlertCategory
    component: str
    check_key: str
    status: str
    severity: InfrastructureAlertSeverity
    summary: str
    last_activity_at: datetime | None = None
    details: str | None = None
    source: str = "application"
    target_url: str | None = None
    metadata: dict[str, Any] | None = None

    @property
    def fingerprint(self) -> str:
        return f"{self.category.value}:{self.check_key}:{self.component}".lower()

    @property
    def creates_alert(self) -> bool:
        return self.status in {"degraded", "unhealthy"} or self.severity != InfrastructureAlertSeverity.info


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _result(
    *,
    category: InfrastructureAlertCategory,
    component: str,
    check_key: str,
    status: str,
    severity: InfrastructureAlertSeverity,
    summary: str,
    last_activity_at: datetime | None = None,
    details: str | None = None,
    source: str = "application",
    target_url: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> HealthCheckResult:
    return HealthCheckResult(
        category=category,
        component=component,
        check_key=check_key,
        status=status,
        severity=severity,
        summary=summary,
        last_activity_at=last_activity_at,
        details=details,
        source=source,
        target_url=target_url,
        metadata=metadata or {},
    )


def _database_check(db: Session) -> HealthCheckResult:
    try:
        db.execute(text("select 1")).scalar_one()
    except Exception as exc:
        return _result(
            category=InfrastructureAlertCategory.database,
            component="primary database",
            check_key="database_connectivity",
            status="unhealthy",
            severity=InfrastructureAlertSeverity.critical,
            summary="Primary database connectivity failed.",
            details=str(exc),
        )
    return _result(
        category=InfrastructureAlertCategory.database,
        component="primary database",
        check_key="database_connectivity",
        status="healthy",
        severity=InfrastructureAlertSeverity.info,
        summary="Primary database accepted a probe query.",
    )


def _replication_check(db: Session) -> HealthCheckResult:
    if db.get_bind().dialect.name != "postgresql":
        return _result(
            category=InfrastructureAlertCategory.replication,
            component="standby database",
            check_key="postgres_replication",
            status="unknown",
            severity=InfrastructureAlertSeverity.info,
            summary="Replication status is only available on PostgreSQL.",
        )
    try:
        row = (
            db.execute(
                text(
                    """
                select
                    count(*)::int as replica_count,
                    max(extract(epoch from coalesce(replay_lag, flush_lag, write_lag, interval '0 second'))) as lag_seconds
                from pg_stat_replication
                """
                )
            )
            .mappings()
            .one()
        )
    except Exception as exc:
        return _result(
            category=InfrastructureAlertCategory.replication,
            component="standby database",
            check_key="postgres_replication",
            status="unhealthy",
            severity=InfrastructureAlertSeverity.warning,
            summary="Could not read PostgreSQL replication status.",
            details=str(exc),
        )

    replica_count = int(row.get("replica_count") or 0)
    lag_seconds = float(row.get("lag_seconds") or 0.0)
    warn_lag = int(os.getenv("INFRA_REPLICATION_WARN_LAG_SECONDS", "300"))
    critical_lag = int(os.getenv("INFRA_REPLICATION_CRITICAL_LAG_SECONDS", "1800"))
    metadata = {"replica_count": replica_count, "lag_seconds": lag_seconds}

    if replica_count < 1:
        return _result(
            category=InfrastructureAlertCategory.replication,
            component="standby database",
            check_key="postgres_replication",
            status="unhealthy",
            severity=InfrastructureAlertSeverity.critical,
            summary="No PostgreSQL standby replica is connected.",
            metadata=metadata,
        )
    if lag_seconds >= critical_lag:
        return _result(
            category=InfrastructureAlertCategory.replication,
            component="standby database",
            check_key="postgres_replication",
            status="unhealthy",
            severity=InfrastructureAlertSeverity.critical,
            summary=f"PostgreSQL standby replication lag is {int(lag_seconds)} seconds.",
            metadata=metadata,
        )
    if lag_seconds >= warn_lag:
        return _result(
            category=InfrastructureAlertCategory.replication,
            component="standby database",
            check_key="postgres_replication",
            status="degraded",
            severity=InfrastructureAlertSeverity.warning,
            summary=f"PostgreSQL standby replication lag is {int(lag_seconds)} seconds.",
            metadata=metadata,
        )
    return _result(
        category=InfrastructureAlertCategory.replication,
        component="standby database",
        check_key="postgres_replication",
        status="healthy",
        severity=InfrastructureAlertSeverity.info,
        summary=f"{replica_count} standby replica is connected with acceptable lag.",
        metadata=metadata,
    )


def _redis_check() -> HealthCheckResult:
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        import redis

        client = redis.Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
        pong = bool(cast(Any, client.ping()))
        info = cast(dict[str, Any], client.info(section="memory"))
    except Exception as exc:
        return _result(
            category=InfrastructureAlertCategory.cache,
            component="redis",
            check_key="redis_connectivity",
            status="unhealthy",
            severity=InfrastructureAlertSeverity.critical,
            summary="Redis/cache connectivity failed.",
            details=str(exc),
        )
    return _result(
        category=InfrastructureAlertCategory.cache,
        component="redis",
        check_key="redis_connectivity",
        status="healthy" if pong else "degraded",
        severity=InfrastructureAlertSeverity.info if pong else InfrastructureAlertSeverity.warning,
        summary="Redis/cache accepted a ping.",
        metadata={"used_memory_human": info.get("used_memory_human")},
    )


def _queue_check() -> HealthCheckResult:
    broker_url = str(os.getenv("CELERY_BROKER_URL") or os.getenv("REDIS_URL") or "redis://localhost:6379/0")
    queue_name = os.getenv("INFRA_CELERY_QUEUE_NAME", "celery")
    warn_depth = int(os.getenv("INFRA_QUEUE_WARN_DEPTH", "100"))
    critical_depth = int(os.getenv("INFRA_QUEUE_CRITICAL_DEPTH", "1000"))
    try:
        import redis

        client = redis.Redis.from_url(broker_url, socket_connect_timeout=2, socket_timeout=2)
        depth = int(cast(Any, client.llen(queue_name)))
    except Exception as exc:
        return _result(
            category=InfrastructureAlertCategory.queues,
            component=queue_name,
            check_key="celery_queue_depth",
            status="unhealthy",
            severity=InfrastructureAlertSeverity.warning,
            summary="Could not inspect Celery queue backlog.",
            details=str(exc),
        )
    if depth >= critical_depth:
        status = "unhealthy"
        severity = InfrastructureAlertSeverity.critical
    elif depth >= warn_depth:
        status = "degraded"
        severity = InfrastructureAlertSeverity.warning
    else:
        status = "healthy"
        severity = InfrastructureAlertSeverity.info
    return _result(
        category=InfrastructureAlertCategory.queues,
        component=queue_name,
        check_key="celery_queue_depth",
        status=status,
        severity=severity,
        summary=f"Celery queue depth is {depth}.",
        metadata={"queue": queue_name, "depth": depth},
    )


def _worker_check() -> HealthCheckResult:
    try:
        from app.celery_app import celery_app

        responses = celery_app.control.inspect(timeout=2).ping() or {}
    except Exception as exc:
        return _result(
            category=InfrastructureAlertCategory.background_workers,
            component="celery workers",
            check_key="celery_workers",
            status="unhealthy",
            severity=InfrastructureAlertSeverity.critical,
            summary="Could not inspect Celery workers.",
            details=str(exc),
        )
    if not responses:
        return _result(
            category=InfrastructureAlertCategory.background_workers,
            component="celery workers",
            check_key="celery_workers",
            status="unhealthy",
            severity=InfrastructureAlertSeverity.critical,
            summary="No Celery workers responded to ping.",
        )
    return _result(
        category=InfrastructureAlertCategory.background_workers,
        component="celery workers",
        check_key="celery_workers",
        status="healthy",
        severity=InfrastructureAlertSeverity.info,
        summary=f"{len(responses)} Celery worker node responded.",
        metadata={"workers": sorted(responses.keys())},
    )


def _scheduled_jobs_check(db: Session) -> HealthCheckResult:
    enabled_count = db.scalar(select(func.count(ScheduledTask.id)).where(ScheduledTask.enabled.is_(True))) or 0
    if enabled_count < 1:
        return _result(
            category=InfrastructureAlertCategory.scheduled_jobs,
            component="celery beat",
            check_key="scheduled_jobs_registered",
            status="degraded",
            severity=InfrastructureAlertSeverity.warning,
            summary="No enabled scheduled jobs are registered.",
        )

    now = _now()
    stale_threshold = now - timedelta(hours=int(os.getenv("INFRA_SCHEDULED_JOB_STALE_HOURS", "24")))
    stale_count = (
        db.scalar(
            select(func.count(ScheduledTask.id)).where(
                ScheduledTask.enabled.is_(True),
                ScheduledTask.last_run_at.is_not(None),
                ScheduledTask.last_run_at < stale_threshold,
            )
        )
        or 0
    )
    if stale_count:
        return _result(
            category=InfrastructureAlertCategory.scheduled_jobs,
            component="celery beat",
            check_key="scheduled_jobs_stale",
            status="degraded",
            severity=InfrastructureAlertSeverity.warning,
            summary=f"{stale_count} enabled scheduled job has stale activity.",
            metadata={"stale_count": int(stale_count), "enabled_count": int(enabled_count)},
        )
    return _result(
        category=InfrastructureAlertCategory.scheduled_jobs,
        component="celery beat",
        check_key="scheduled_jobs_registered",
        status="healthy",
        severity=InfrastructureAlertSeverity.info,
        summary=f"{enabled_count} scheduled jobs are registered.",
        metadata={"enabled_count": int(enabled_count)},
    )


def _application_service_check() -> HealthCheckResult:
    return _result(
        category=InfrastructureAlertCategory.application_services,
        component="admin web app",
        check_key="application_process",
        status="healthy",
        severity=InfrastructureAlertSeverity.info,
        summary="Application process is serving the health check task.",
    )


def _external_integrations_check(db: Session) -> HealthCheckResult:
    zabbix = db.scalar(
        select(ConnectorConfig)
        .where(ConnectorConfig.connector_type == ConnectorType.zabbix, ConnectorConfig.is_active.is_(True))
        .order_by(ConnectorConfig.updated_at.desc())
        .limit(1)
    )
    if not zabbix:
        return _result(
            category=InfrastructureAlertCategory.external_integrations,
            component="zabbix",
            check_key="zabbix_connector",
            status="unknown",
            severity=InfrastructureAlertSeverity.info,
            summary="No active Zabbix connector is configured.",
            source="zabbix",
            target_url="/admin/integrations/connectors",
        )
    try:
        from app.services import zabbix as zabbix_service

        rows = zabbix_service.fetch_monitoring_devices(db)
    except Exception as exc:
        return _result(
            category=InfrastructureAlertCategory.external_integrations,
            component="zabbix",
            check_key="zabbix_connector",
            status="degraded",
            severity=InfrastructureAlertSeverity.warning,
            summary="Zabbix connector is configured but could not be queried.",
            details=str(exc),
            source="zabbix",
            target_url="/admin/integrations/connectors",
        )
    return _result(
        category=InfrastructureAlertCategory.external_integrations,
        component="zabbix",
        check_key="zabbix_connector",
        status="healthy",
        severity=InfrastructureAlertSeverity.info,
        summary=f"Zabbix connector returned {len(rows)} monitored hosts.",
        source="zabbix",
        target_url="/admin/integrations/connectors",
        metadata={"host_count": len(rows)},
    )


def collect_health(db: Session) -> list[HealthCheckResult]:
    checks = [
        lambda: _application_service_check(),
        lambda: _database_check(db),
        lambda: _replication_check(db),
        lambda: _redis_check(),
        lambda: _queue_check(),
        lambda: _worker_check(),
        lambda: _scheduled_jobs_check(db),
        lambda: _external_integrations_check(db),
    ]
    results: list[HealthCheckResult] = []
    for check in checks:
        try:
            results.append(check())
        except Exception as exc:
            results.append(
                _result(
                    category=InfrastructureAlertCategory.application_services,
                    component="health check runner",
                    check_key="health_check_runner",
                    status="unhealthy",
                    severity=InfrastructureAlertSeverity.critical,
                    summary="Infrastructure health check runner failed.",
                    details=str(exc),
                )
            )
    return results


def _recipient_person_ids(db: Session) -> list[str]:
    permission_keys = [MONITORING_READ_PERMISSION, "system:settings:read", "system:read"]
    direct_ids = (
        select(PersonPermission.person_id)
        .join(Permission, Permission.id == PersonPermission.permission_id)
        .where(Permission.key.in_(permission_keys), Permission.is_active.is_(True))
    )
    role_ids = (
        select(PersonRole.person_id)
        .join(Role, Role.id == PersonRole.role_id)
        .outerjoin(RolePermission, RolePermission.role_id == Role.id)
        .outerjoin(Permission, Permission.id == RolePermission.permission_id)
        .where(
            Role.is_active.is_(True),
            (Role.name == "admin") | ((Permission.key.in_(permission_keys)) & (Permission.is_active.is_(True))),
        )
    )
    rows = db.execute(
        select(Person.id)
        .where(Person.is_active.is_(True), Person.id.in_(direct_ids.union(role_ids)))
        .order_by(Person.created_at.asc())
    ).scalars()
    return [str(person_id) for person_id in rows]


def _notify_alert(db: Session, alert: InfrastructureAlert, *, event: str) -> int:
    recipients = _recipient_person_ids(db)
    if not recipients:
        return 0
    target_url = alert.target_url or f"/admin/system/health/alerts?status={alert.status.value}"
    subject = f"Infrastructure alert {event}: {alert.severity.value} - {alert.component}"
    body = f"{alert.summary}\n\nCategory: {alert.category.value.replace('_', ' ')}\nOpen: {target_url}"
    for recipient in recipients:
        db.add(
            Notification(
                channel=NotificationChannel.push,
                recipient=recipient,
                subject=subject[:200],
                body=body,
                status=NotificationStatus.delivered,
                sent_at=_now(),
            )
        )
    alert.last_notified_at = _now()
    return len(recipients)


def upsert_alerts_from_results(db: Session, results: list[HealthCheckResult]) -> dict[str, int]:
    now = _now()
    active_results = [result for result in results if result.creates_alert]
    active_fingerprints = {result.fingerprint for result in active_results}
    check_keys = {result.check_key for result in results}
    created = reopened = escalated = resolved = notified = 0

    for result in active_results:
        alert = db.scalar(select(InfrastructureAlert).where(InfrastructureAlert.fingerprint == result.fingerprint))
        event: str | None = None
        if alert is None:
            alert = InfrastructureAlert(
                fingerprint=result.fingerprint,
                category=result.category,
                component=result.component,
                severity=result.severity,
                status=InfrastructureAlertStatus.open,
                summary=result.summary,
                details=result.details,
                source=result.source,
                check_key=result.check_key,
                target_url=result.target_url,
                metadata_=result.metadata,
                occurrence_count=1,
                first_seen_at=now,
                last_seen_at=now,
            )
            db.add(alert)
            created += 1
            event = "new"
        else:
            previous_status = alert.status
            previous_severity = alert.severity
            alert.category = result.category
            alert.component = result.component
            alert.summary = result.summary
            alert.details = result.details
            alert.source = result.source
            alert.check_key = result.check_key
            alert.target_url = result.target_url
            alert.metadata_ = result.metadata
            alert.last_seen_at = now
            alert.occurrence_count = int(alert.occurrence_count or 0) + 1
            if previous_status == InfrastructureAlertStatus.resolved:
                alert.status = InfrastructureAlertStatus.open
                alert.resolved_at = None
                alert.first_seen_at = now
                reopened += 1
                event = "reopened"
            if _SEVERITY_RANK[result.severity] > _SEVERITY_RANK.get(previous_severity, 0):
                escalated += 1
                event = "escalated"
            alert.severity = result.severity

        db.flush()
        if event is not None:
            notified += _notify_alert(db, alert, event=event)

    open_alerts = db.execute(
        select(InfrastructureAlert).where(
            InfrastructureAlert.status == InfrastructureAlertStatus.open,
            InfrastructureAlert.check_key.in_(check_keys),
        )
    ).scalars()
    for alert in open_alerts:
        if alert.fingerprint in active_fingerprints:
            continue
        alert.status = InfrastructureAlertStatus.resolved
        alert.resolved_at = now
        alert.last_seen_at = now
        resolved += 1

    db.commit()
    return {
        "created": created,
        "reopened": reopened,
        "escalated": escalated,
        "resolved": resolved,
        "notified": notified,
    }


def run_health_checks(db: Session) -> dict[str, Any]:
    results = collect_health(db)
    stats = upsert_alerts_from_results(db, results)
    return {
        **stats,
        "checked": len(results),
        "unhealthy": sum(1 for result in results if result.creates_alert),
    }


def health_dashboard(db: Session) -> dict[str, Any]:
    results = collect_health(db)
    alerts = (
        db.execute(
            select(InfrastructureAlert)
            .where(InfrastructureAlert.status == InfrastructureAlertStatus.open)
            .order_by(InfrastructureAlert.severity.desc(), InfrastructureAlert.last_seen_at.desc())
            .limit(10)
        )
        .scalars()
        .all()
    )
    status_order = {"unhealthy": 2, "degraded": 1, "unknown": 0, "healthy": 0}
    overall = "healthy"
    if any(result.status == "unhealthy" for result in results) or any(
        alert.severity == InfrastructureAlertSeverity.critical for alert in alerts
    ):
        overall = "critical"
    elif any(result.status == "degraded" for result in results) or alerts:
        overall = "warning"
    return {
        "generated_at": _now(),
        "overall": overall,
        "results": sorted(results, key=lambda result: (result.category.value, -status_order.get(result.status, 0))),
        "open_alerts": alerts,
        "open_alert_count": len(alerts),
    }


def list_alerts(
    db: Session,
    *,
    category: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    period_days: int = 30,
    limit: int = 100,
    offset: int = 0,
) -> list[InfrastructureAlert]:
    query = select(InfrastructureAlert)
    if category:
        query = query.where(InfrastructureAlert.category == InfrastructureAlertCategory(category))
    if severity:
        query = query.where(InfrastructureAlert.severity == InfrastructureAlertSeverity(severity))
    if status:
        query = query.where(InfrastructureAlert.status == InfrastructureAlertStatus(status))
    if period_days > 0:
        query = query.where(InfrastructureAlert.last_seen_at >= _now() - timedelta(days=period_days))
    return list(
        db.execute(query.order_by(InfrastructureAlert.last_seen_at.desc()).limit(limit).offset(offset)).scalars().all()
    )


def alert_summary(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(InfrastructureAlert.severity, func.count(InfrastructureAlert.id))
        .where(InfrastructureAlert.status == InfrastructureAlertStatus.open)
        .group_by(InfrastructureAlert.severity)
    ).all()
    counts = {severity.value: int(count) for severity, count in rows}
    return {
        "open": sum(counts.values()),
        "critical": counts.get("critical", 0),
        "warning": counts.get("warning", 0),
        "info": counts.get("info", 0),
    }
