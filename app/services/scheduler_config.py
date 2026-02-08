import logging
import os
from datetime import timedelta

from app.db import SessionLocal
from app.models.domain_settings import DomainSetting, SettingDomain
from app.models.scheduler import ScheduleType, ScheduledTask
from app.services import integration as integration_service
from app.services.settings_spec import resolve_value

logger = logging.getLogger(__name__)


def _env_value(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    return value


def _env_bool(name: str) -> bool | None:
    raw = _env_value(name)
    if raw is None:
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str) -> int | None:
    raw = _env_value(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _get_setting_value(db, domain: SettingDomain, key: str) -> str | None:
    setting = (
        db.query(DomainSetting)
        .filter(DomainSetting.domain == domain)
        .filter(DomainSetting.key == key)
        .filter(DomainSetting.is_active.is_(True))
        .first()
    )
    if not setting:
        return None
    if setting.value_text:
        return setting.value_text
    if setting.value_json is not None:
        return str(setting.value_json)
    return None


def _effective_bool(
    db, domain: SettingDomain, key: str, env_key: str, default: bool
) -> bool:
    env_value = _env_bool(env_key)
    if env_value is not None:
        return env_value
    value = _get_setting_value(db, domain, key)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _effective_int(
    db, domain: SettingDomain, key: str, env_key: str, default: int
) -> int:
    env_value = _env_int(env_key)
    if env_value is not None:
        return env_value
    value = _get_setting_value(db, domain, key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _effective_str(
    db, domain: SettingDomain, key: str, env_key: str, default: str | None
) -> str | None:
    env_value = _env_value(env_key)
    if env_value is not None:
        return env_value
    value = _get_setting_value(db, domain, key)
    if value is None:
        return default
    return str(value)


def _coerce_int(value: object | None, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    if value is None:
        return default
    if isinstance(value, float):
        return int(value)
    return default


def _sync_scheduled_task(
    db,
    name: str,
    task_name: str,
    enabled: bool,
    interval_seconds: int,
) -> None:
    task = (
        db.query(ScheduledTask)
        .filter(ScheduledTask.task_name == task_name)
        .order_by(ScheduledTask.created_at.desc())
        .first()
    )
    if not task:
        if not enabled:
            return
        task = ScheduledTask(
            name=name,
            task_name=task_name,
            schedule_type=ScheduleType.interval,
            interval_seconds=interval_seconds,
            enabled=True,
        )
        db.add(task)
        db.commit()
        return
    changed = False
    if task.name != name:
        task.name = name
        changed = True
    if task.interval_seconds != interval_seconds:
        task.interval_seconds = interval_seconds
        changed = True
    if task.enabled != enabled:
        task.enabled = enabled
        changed = True
    if changed:
        db.commit()


def get_celery_config() -> dict:
    broker = None
    backend = None
    timezone = None
    beat_max_loop_interval = 5
    beat_refresh_seconds = 30
    session = SessionLocal()
    try:
        broker = _effective_str(
            session, SettingDomain.scheduler, "broker_url", "CELERY_BROKER_URL", None
        )
        backend = _effective_str(
            session,
            SettingDomain.scheduler,
            "result_backend",
            "CELERY_RESULT_BACKEND",
            None,
        )
        timezone = _effective_str(
            session, SettingDomain.scheduler, "timezone", "CELERY_TIMEZONE", None
        )
        beat_max_loop_interval = _effective_int(
            session,
            SettingDomain.scheduler,
            "beat_max_loop_interval",
            "CELERY_BEAT_MAX_LOOP_INTERVAL",
            5,
        )
        beat_refresh_seconds = _effective_int(
            session,
            SettingDomain.scheduler,
            "beat_refresh_seconds",
            "CELERY_BEAT_REFRESH_SECONDS",
            30,
        )
    except Exception:
        logger.exception("Failed to load scheduler settings from database.")
    finally:
        session.close()

    broker = (
        broker
        or _env_value("REDIS_URL")
        or "redis://localhost:6379/0"
    )
    backend = (
        backend
        or _env_value("REDIS_URL")
        or "redis://localhost:6379/1"
    )
    timezone = timezone or "UTC"
    config: dict[str, object] = {
        "broker_url": broker,
        "result_backend": backend,
        "timezone": timezone,
    }
    config["beat_max_loop_interval"] = beat_max_loop_interval
    config["beat_refresh_seconds"] = beat_refresh_seconds
    return config


def build_beat_schedule() -> dict:
    schedule: dict[str, dict] = {}
    session = SessionLocal()
    try:
        enabled = _effective_bool(
            session, SettingDomain.gis, "sync_enabled", "GIS_SYNC_ENABLED", True
        )
        interval_minutes = _effective_int(
            session,
            SettingDomain.gis,
            "sync_interval_minutes",
            "GIS_SYNC_INTERVAL_MINUTES",
            60,
        )
        if enabled:
            schedule["gis_sync"] = {
                "task": "app.tasks.gis.sync_gis_sources",
                "schedule": timedelta(minutes=max(interval_minutes, 1)),
            }

        # Note: Legacy scheduled tasks removed (usage_rating, billing, dunning, prepaid, subscription_expiration)

        notification_queue_enabled = _effective_bool(
            session,
            SettingDomain.notification,
            "notification_queue_enabled",
            "NOTIFICATION_QUEUE_ENABLED",
            True,
        )
        notification_queue_interval_seconds = _effective_int(
            session,
            SettingDomain.notification,
            "notification_queue_interval_seconds",
            "NOTIFICATION_QUEUE_INTERVAL_SECONDS",
            60,
        )
        notification_queue_interval_seconds = max(
            notification_queue_interval_seconds, 30
        )
        _sync_scheduled_task(
            session,
            name="notification_queue_runner",
            task_name="app.tasks.notifications.deliver_notification_queue",
            enabled=notification_queue_enabled,
            interval_seconds=notification_queue_interval_seconds,
        )
        retention_enabled = _effective_bool(
            session,
            SettingDomain.catalog,
            "nas_backup_retention_enabled",
            "NAS_BACKUP_RETENTION_ENABLED",
            True,
        )
        retention_interval_seconds = _coerce_int(
            resolve_value(
                session,
                SettingDomain.provisioning,
                "nas_backup_retention_interval_seconds",
            ),
            86400,
        )
        retention_interval_seconds = max(retention_interval_seconds, 3600)
        _sync_scheduled_task(
            session,
            name="nas_backup_retention_cleanup",
            task_name="app.tasks.nas.cleanup_nas_backups",
            enabled=retention_enabled,
            interval_seconds=retention_interval_seconds,
        )
        # OAuth token refresh - runs daily to proactively refresh expiring tokens
        oauth_refresh_enabled = _effective_bool(
            session,
            SettingDomain.comms,
            "oauth_token_refresh_enabled",
            "OAUTH_TOKEN_REFRESH_ENABLED",
            True,
        )
        oauth_refresh_interval_seconds = _coerce_int(
            resolve_value(
                session,
                SettingDomain.provisioning,
                "oauth_token_refresh_interval_seconds",
            ),
            86400,
        )
        oauth_refresh_interval_seconds = max(oauth_refresh_interval_seconds, 3600)  # Min: 1 hour
        _sync_scheduled_task(
            session,
            name="oauth_token_refresh",
            task_name="app.tasks.oauth.refresh_expiring_tokens",
            enabled=oauth_refresh_enabled,
            interval_seconds=oauth_refresh_interval_seconds,
        )
        integration_jobs = integration_service.list_interval_jobs(session)
        if not integration_jobs:
            logger.info("EMAIL_POLL_EXIT reason=no_jobs")
        for job in integration_jobs:
            interval_seconds = job.interval_seconds
            if interval_seconds is None and job.interval_minutes:
                interval_seconds = job.interval_minutes * 60
            interval_seconds = max(interval_seconds or 0, 1)
            schedule[f"integration_job_{job.id}"] = {
                "task": "app.tasks.integrations.run_integration_job",
                "schedule": timedelta(seconds=interval_seconds),
                "args": [str(job.id)],
            }

        # Bandwidth monitoring tasks
        bandwidth_enabled = _effective_bool(
            session,
            SettingDomain.bandwidth,
            "bandwidth_processing_enabled",
            "BANDWIDTH_PROCESSING_ENABLED",
            True,
        )
        if bandwidth_enabled:
            # Process bandwidth stream - runs every 5 seconds
            bandwidth_stream_interval = _coerce_int(
                resolve_value(session, SettingDomain.bandwidth, "stream_interval_seconds"),
                5,
            )
            _sync_scheduled_task(
                session,
                name="bandwidth_stream_processor",
                task_name="app.tasks.bandwidth.process_bandwidth_stream",
                enabled=bandwidth_enabled,
                interval_seconds=max(bandwidth_stream_interval, 1),
            )

            # Aggregate to VictoriaMetrics - runs every minute
            aggregate_interval = _coerce_int(
                resolve_value(session, SettingDomain.bandwidth, "aggregate_interval_seconds"),
                60,
            )
            _sync_scheduled_task(
                session,
                name="bandwidth_aggregate_to_metrics",
                task_name="app.tasks.bandwidth.aggregate_to_metrics",
                enabled=bandwidth_enabled,
                interval_seconds=max(aggregate_interval, 10),
            )

            # Cleanup hot data - runs hourly
            cleanup_interval = _coerce_int(
                resolve_value(session, SettingDomain.bandwidth, "cleanup_interval_seconds"),
                3600,
            )
            _sync_scheduled_task(
                session,
                name="bandwidth_cleanup_hot_data",
                task_name="app.tasks.bandwidth.cleanup_hot_data",
                enabled=bandwidth_enabled,
                interval_seconds=max(cleanup_interval, 60),
            )

            # Trim Redis stream - runs every 10 minutes
            trim_interval = _coerce_int(
                resolve_value(session, SettingDomain.bandwidth, "trim_interval_seconds"),
                600,
            )
            _sync_scheduled_task(
                session,
                name="bandwidth_trim_stream",
                task_name="app.tasks.bandwidth.trim_redis_stream",
                enabled=bandwidth_enabled,
                interval_seconds=max(trim_interval, 60),
            )

        # Note: SNMP tasks removed (snmp_interface_walk, snmp_interface_discovery)

        # SLA breach detection - runs every 30 minutes
        sla_breach_enabled = _effective_bool(
            session,
            SettingDomain.workflow,
            "sla_breach_detection_enabled",
            "SLA_BREACH_DETECTION_ENABLED",
            True,
        )
        sla_breach_interval_seconds = _coerce_int(
            resolve_value(
                session, SettingDomain.workflow, "sla_breach_detection_interval_seconds"
            ),
            1800,
        )
        sla_breach_min_interval = _coerce_int(
            resolve_value(
                session, SettingDomain.workflow, "sla_breach_detection_min_interval"
            ),
            60,
        )
        sla_breach_interval_seconds = max(sla_breach_interval_seconds, sla_breach_min_interval)
        _sync_scheduled_task(
            session,
            name="sla_breach_detection",
            task_name="app.tasks.workflow.detect_sla_breaches",
            enabled=sla_breach_enabled,
            interval_seconds=sla_breach_interval_seconds,
        )

        # Campaign scheduled send - checks for campaigns due to send
        _sync_scheduled_task(
            session,
            name="campaign_scheduled_send",
            task_name="app.tasks.campaigns.process_scheduled_campaigns",
            enabled=True,
            interval_seconds=60,
        )

        # Campaign nurture steps - checks for due nurture steps
        _sync_scheduled_task(
            session,
            name="campaign_nurture_steps",
            task_name="app.tasks.campaigns.process_nurture_steps",
            enabled=True,
            interval_seconds=300,
        )

        # CRM inbox reply reminders - checks for unreplied inbound messages
        reminder_interval_seconds = 60
        _sync_scheduled_task(
            session,
            name="crm_inbox_reply_reminders",
            task_name="app.tasks.crm_inbox.send_reply_reminders",
            enabled=True,
            interval_seconds=reminder_interval_seconds,
        )

        # CRM inbox outbox queue runner
        outbox_interval_seconds = 30
        _sync_scheduled_task(
            session,
            name="crm_inbox_outbox_queue",
            task_name="app.tasks.crm_inbox.process_outbox_queue",
            enabled=True,
            interval_seconds=outbox_interval_seconds,
        )

        # Event retry - retries failed event handlers
        event_retry_enabled = _effective_bool(
            session,
            SettingDomain.scheduler,
            "event_retry_enabled",
            "EVENT_RETRY_ENABLED",
            True,
        )
        event_retry_interval = _coerce_int(
            resolve_value(
                session, SettingDomain.scheduler, "event_retry_interval_seconds"
            ),
            300,
        )  # Default: 5 minutes
        event_retry_interval = max(event_retry_interval, 60)  # Min: 1 minute
        _sync_scheduled_task(
            session,
            name="event_retry_runner",
            task_name="app.tasks.events.retry_failed_events",
            enabled=event_retry_enabled,
            interval_seconds=event_retry_interval,
        )

        # Event stale processing cleanup - marks stuck events as failed
        event_stale_cleanup_enabled = _effective_bool(
            session,
            SettingDomain.scheduler,
            "event_stale_cleanup_enabled",
            "EVENT_STALE_CLEANUP_ENABLED",
            True,
        )
        event_stale_cleanup_interval = _coerce_int(
            resolve_value(
                session, SettingDomain.scheduler, "event_stale_cleanup_interval_seconds"
            ),
            600,
        )  # Default: 10 minutes
        event_stale_cleanup_interval = max(event_stale_cleanup_interval, 60)  # Min: 1 minute
        _sync_scheduled_task(
            session,
            name="event_stale_cleanup_runner",
            task_name="app.tasks.events.mark_stale_processing_events",
            enabled=event_stale_cleanup_enabled,
            interval_seconds=event_stale_cleanup_interval,
        )

        # Event old cleanup - removes old completed events
        event_old_cleanup_enabled = _effective_bool(
            session,
            SettingDomain.scheduler,
            "event_old_cleanup_enabled",
            "EVENT_OLD_CLEANUP_ENABLED",
            True,
        )
        event_old_cleanup_interval = _coerce_int(
            resolve_value(
                session, SettingDomain.scheduler, "event_old_cleanup_interval_seconds"
            ),
            86400,
        )  # Default: daily
        event_old_cleanup_interval = max(event_old_cleanup_interval, 3600)  # Min: 1 hour
        _sync_scheduled_task(
            session,
            name="event_old_cleanup_runner",
            task_name="app.tasks.events.cleanup_old_events",
            enabled=event_old_cleanup_enabled,
            interval_seconds=event_old_cleanup_interval,
        )

        # DotMac ERP sync - pushes projects, tickets, work orders to ERP
        dotmac_erp_sync_enabled = _effective_bool(
            session,
            SettingDomain.integration,
            "dotmac_erp_sync_enabled",
            "DOTMAC_ERP_SYNC_ENABLED",
            False,
        )
        dotmac_erp_sync_interval_minutes = _coerce_int(
            resolve_value(
                session, SettingDomain.integration, "dotmac_erp_sync_interval_minutes"
            ),
            60,
        )
        dotmac_erp_sync_interval_seconds = max(dotmac_erp_sync_interval_minutes * 60, 300)
        _sync_scheduled_task(
            session,
            name="dotmac_erp_sync",
            task_name="app.tasks.integrations.sync_dotmac_erp",
            enabled=dotmac_erp_sync_enabled,
            interval_seconds=dotmac_erp_sync_interval_seconds,
        )

        # DotMac ERP shift sync - pulls technician shifts and time-off from ERP
        dotmac_erp_shift_sync_enabled = _effective_bool(
            session,
            SettingDomain.integration,
            "dotmac_erp_shift_sync_enabled",
            "DOTMAC_ERP_SHIFT_SYNC_ENABLED",
            False,
        )
        dotmac_erp_shift_sync_interval_minutes = _coerce_int(
            resolve_value(
                session, SettingDomain.integration, "dotmac_erp_shift_sync_interval_minutes"
            ),
            60,  # Default: hourly
        )
        dotmac_erp_shift_sync_interval_seconds = max(dotmac_erp_shift_sync_interval_minutes * 60, 300)
        _sync_scheduled_task(
            session,
            name="dotmac_erp_shift_sync",
            task_name="app.tasks.integrations.sync_dotmac_erp_shifts",
            enabled=dotmac_erp_shift_sync_enabled,
            interval_seconds=dotmac_erp_shift_sync_interval_seconds,
        )

        # Survey triggers - checks for ticket_closed / work_order_completed triggers
        _sync_scheduled_task(
            session,
            name="survey_triggers",
            task_name="app.tasks.surveys.process_survey_triggers",
            enabled=True,
            interval_seconds=60,
        )

        # Chatwoot CRM sync - imports contacts, conversations, messages from Chatwoot
        chatwoot_sync_enabled = _effective_bool(
            session,
            SettingDomain.integration,
            "chatwoot_sync_enabled",
            "CHATWOOT_SYNC_ENABLED",
            False,
        )
        chatwoot_sync_interval_minutes = _coerce_int(
            resolve_value(
                session, SettingDomain.integration, "chatwoot_sync_interval_minutes"
            ),
            60,
        )
        chatwoot_sync_interval_seconds = max(chatwoot_sync_interval_minutes * 60, 300)
        _sync_scheduled_task(
            session,
            name="chatwoot_crm_sync",
            task_name="app.tasks.integrations.sync_chatwoot",
            enabled=chatwoot_sync_enabled,
            interval_seconds=chatwoot_sync_interval_seconds,
        )

        tasks = (
            session.query(ScheduledTask)
            .filter(ScheduledTask.enabled.is_(True))
            .all()
        )
        for task in tasks:
            if task.schedule_type != ScheduleType.interval:
                continue
            interval_seconds = max(task.interval_seconds or 0, 1)
            schedule[f"scheduled_task_{task.id}"] = {
                "task": task.task_name,
                "schedule": timedelta(seconds=interval_seconds),
                "args": task.args_json or [],
                "kwargs": task.kwargs_json or {},
            }
    except Exception:
        logger.exception("Failed to build Celery beat schedule.")
    finally:
        session.close()
    return schedule
