import json
import os

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingValueType
from app.services.domain_settings import (
    auth_settings,
    audit_settings,
    geocoding_settings,
    imports_settings,
    inventory_settings,
    gis_settings,
    comms_settings,
    network_settings,
    notification_settings,
    projects_settings,
    provisioning_settings,
    scheduler_settings,
    workflow_settings,
)
from app.services.secrets import is_openbao_ref


def seed_auth_settings(db: Session) -> None:
    auth_settings.ensure_by_key(
        db,
        key="jwt_algorithm",
        value_type=SettingValueType.string,
        value_text=os.getenv("JWT_ALGORITHM", "HS256"),
    )
    auth_settings.ensure_by_key(
        db,
        key="jwt_access_ttl_minutes",
        value_type=SettingValueType.integer,
        value_text=os.getenv("JWT_ACCESS_TTL_MINUTES", "15"),
    )
    auth_settings.ensure_by_key(
        db,
        key="jwt_refresh_ttl_days",
        value_type=SettingValueType.integer,
        value_text=os.getenv("JWT_REFRESH_TTL_DAYS", "30"),
    )
    auth_settings.ensure_by_key(
        db,
        key="refresh_cookie_name",
        value_type=SettingValueType.string,
        value_text=os.getenv("REFRESH_COOKIE_NAME", "refresh_token"),
    )
    auth_settings.ensure_by_key(
        db,
        key="refresh_cookie_secure",
        value_type=SettingValueType.boolean,
        value_text=os.getenv("REFRESH_COOKIE_SECURE", "false"),
        value_json=os.getenv("REFRESH_COOKIE_SECURE", "false").lower()
        in {"1", "true", "yes", "on"},
    )
    auth_settings.ensure_by_key(
        db,
        key="refresh_cookie_samesite",
        value_type=SettingValueType.string,
        value_text=os.getenv("REFRESH_COOKIE_SAMESITE", "lax"),
    )
    auth_settings.ensure_by_key(
        db,
        key="refresh_cookie_domain",
        value_type=SettingValueType.string,
        value_text=os.getenv("REFRESH_COOKIE_DOMAIN", ""),
    )
    auth_settings.ensure_by_key(
        db,
        key="refresh_cookie_path",
        value_type=SettingValueType.string,
        value_text=os.getenv("REFRESH_COOKIE_PATH", "/auth"),
    )
    auth_settings.ensure_by_key(
        db,
        key="totp_issuer",
        value_type=SettingValueType.string,
        value_text=os.getenv("TOTP_ISSUER", "dotmac_crm"),
    )
    auth_settings.ensure_by_key(
        db,
        key="api_key_rate_window_seconds",
        value_type=SettingValueType.integer,
        value_text=os.getenv("API_KEY_RATE_WINDOW_SECONDS", "60"),
    )
    auth_settings.ensure_by_key(
        db,
        key="api_key_rate_max",
        value_type=SettingValueType.integer,
        value_text=os.getenv("API_KEY_RATE_MAX", "5"),
    )
    jwt_secret = os.getenv("JWT_SECRET")
    if jwt_secret and is_openbao_ref(jwt_secret):
        auth_settings.ensure_by_key(
            db,
            key="jwt_secret",
            value_type=SettingValueType.string,
            value_text=jwt_secret,
            is_secret=True,
        )
    totp_key = os.getenv("TOTP_ENCRYPTION_KEY")
    if totp_key and is_openbao_ref(totp_key):
        auth_settings.ensure_by_key(
            db,
            key="totp_encryption_key",
            value_type=SettingValueType.string,
            value_text=totp_key,
            is_secret=True,
        )


def seed_audit_settings(db: Session) -> None:
    audit_settings.ensure_by_key(
        db,
        key="enabled",
        value_type=SettingValueType.boolean,
        value_text="true",
        value_json=True,
    )
    audit_settings.ensure_by_key(
        db,
        key="methods",
        value_type=SettingValueType.json,
        value_json=["POST", "PUT", "PATCH", "DELETE"],
    )
    audit_settings.ensure_by_key(
        db,
        key="skip_paths",
        value_type=SettingValueType.json,
        value_json=["/static", "/web", "/health"],
    )
    audit_settings.ensure_by_key(
        db,
        key="read_trigger_header",
        value_type=SettingValueType.string,
        value_text="x-audit-read",
    )
    audit_settings.ensure_by_key(
        db,
        key="read_trigger_query",
        value_type=SettingValueType.string,
        value_text="audit",
    )


def seed_imports_settings(db: Session) -> None:
    imports_settings.ensure_by_key(
        db,
        key="max_file_bytes",
        value_type=SettingValueType.integer,
        value_text=str(5 * 1024 * 1024),
    )
    imports_settings.ensure_by_key(
        db,
        key="max_rows",
        value_type=SettingValueType.integer,
        value_text="5000",
    )


def seed_gis_settings(db: Session) -> None:
    gis_settings.ensure_by_key(
        db,
        key="sync_enabled",
        value_type=SettingValueType.boolean,
        value_text="true",
        value_json=True,
    )
    gis_settings.ensure_by_key(
        db,
        key="sync_interval_minutes",
        value_type=SettingValueType.integer,
        value_text="60",
    )
    gis_settings.ensure_by_key(
        db,
        key="sync_pop_sites",
        value_type=SettingValueType.boolean,
        value_text="true",
        value_json=True,
    )
    gis_settings.ensure_by_key(
        db,
        key="sync_addresses",
        value_type=SettingValueType.boolean,
        value_text="true",
        value_json=True,
    )
    gis_settings.ensure_by_key(
        db,
        key="sync_deactivate_missing",
        value_type=SettingValueType.boolean,
        value_text="false",
        value_json=False,
    )
    gis_settings.ensure_by_key(
        db,
        key="map_customer_limit",
        value_type=SettingValueType.integer,
        value_text="2000",
    )
    gis_settings.ensure_by_key(
        db,
        key="map_nearest_search_max_km",
        value_type=SettingValueType.integer,
        value_text="50",
    )
    gis_settings.ensure_by_key(
        db,
        key="map_snap_max_m",
        value_type=SettingValueType.integer,
        value_text="250",
    )
    gis_settings.ensure_by_key(
        db,
        key="map_allow_straightline_fallback",
        value_type=SettingValueType.boolean,
        value_text="false",
    )


def seed_notification_settings(db: Session) -> None:
    enabled_raw = os.getenv("ALERT_NOTIFICATIONS_ENABLED", "true")
    notification_settings.ensure_by_key(
        db,
        key="alert_notifications_enabled",
        value_type=SettingValueType.boolean,
        value_text=enabled_raw,
        value_json=enabled_raw.lower() in {"1", "true", "yes", "on"},
    )
    notification_settings.ensure_by_key(
        db,
        key="alert_notifications_default_channel",
        value_type=SettingValueType.string,
        value_text=os.getenv("ALERT_NOTIFICATIONS_DEFAULT_CHANNEL", "email"),
    )
    notification_settings.ensure_by_key(
        db,
        key="alert_notifications_default_recipient",
        value_type=SettingValueType.string,
        value_text=os.getenv("ALERT_NOTIFICATIONS_DEFAULT_RECIPIENT", ""),
    )
    notification_settings.ensure_by_key(
        db,
        key="alert_notifications_default_template_id",
        value_type=SettingValueType.string,
        value_text=os.getenv("ALERT_NOTIFICATIONS_DEFAULT_TEMPLATE_ID", ""),
    )
    notification_settings.ensure_by_key(
        db,
        key="alert_notifications_default_rotation_id",
        value_type=SettingValueType.string,
        value_text=os.getenv("ALERT_NOTIFICATIONS_DEFAULT_ROTATION_ID", ""),
    )
    notification_settings.ensure_by_key(
        db,
        key="alert_notifications_default_delay_minutes",
        value_type=SettingValueType.integer,
        value_text=os.getenv("ALERT_NOTIFICATIONS_DEFAULT_DELAY_MINUTES", "0"),
    )
    queue_enabled_raw = os.getenv("NOTIFICATION_QUEUE_ENABLED", "true")
    notification_settings.ensure_by_key(
        db,
        key="notification_queue_enabled",
        value_type=SettingValueType.boolean,
        value_text=queue_enabled_raw,
        value_json=queue_enabled_raw.lower() in {"1", "true", "yes", "on"},
    )
    notification_settings.ensure_by_key(
        db,
        key="notification_queue_interval_seconds",
        value_type=SettingValueType.integer,
        value_text=os.getenv("NOTIFICATION_QUEUE_INTERVAL_SECONDS", "60"),
    )


def seed_geocoding_settings(db: Session) -> None:
    geocoding_settings.ensure_by_key(
        db,
        key="enabled",
        value_type=SettingValueType.boolean,
        value_text="true",
        value_json=True,
    )
    geocoding_settings.ensure_by_key(
        db,
        key="provider",
        value_type=SettingValueType.string,
        value_text=os.getenv("GEOCODING_PROVIDER", "nominatim"),
    )
    geocoding_settings.ensure_by_key(
        db,
        key="base_url",
        value_type=SettingValueType.string,
        value_text=os.getenv(
            "GEOCODING_BASE_URL", "https://nominatim.openstreetmap.org"
        ),
    )
    geocoding_settings.ensure_by_key(
        db,
        key="user_agent",
        value_type=SettingValueType.string,
        value_text=os.getenv("GEOCODING_USER_AGENT", "dotmac_crm"),
    )
    geocoding_settings.ensure_by_key(
        db,
        key="email",
        value_type=SettingValueType.string,
        value_text=os.getenv("GEOCODING_EMAIL", ""),
    )
    geocoding_settings.ensure_by_key(
        db,
        key="timeout_sec",
        value_type=SettingValueType.integer,
        value_text=os.getenv("GEOCODING_TIMEOUT_SEC", "5"),
    )


def seed_scheduler_settings(db: Session) -> None:
    broker = (
        os.getenv("CELERY_BROKER_URL")
        or os.getenv("REDIS_URL")
        or "redis://localhost:6379/0"
    )
    backend = (
        os.getenv("CELERY_RESULT_BACKEND")
        or os.getenv("REDIS_URL")
        or "redis://localhost:6379/1"
    )
    scheduler_settings.ensure_by_key(
        db,
        key="broker_url",
        value_type=SettingValueType.string,
        value_text=broker,
    )
    scheduler_settings.ensure_by_key(
        db,
        key="result_backend",
        value_type=SettingValueType.string,
        value_text=backend,
    )
    scheduler_settings.ensure_by_key(
        db,
        key="timezone",
        value_type=SettingValueType.string,
        value_text=os.getenv("CELERY_TIMEZONE", "UTC"),
    )
    scheduler_settings.ensure_by_key(
        db,
        key="beat_max_loop_interval",
        value_type=SettingValueType.integer,
        value_text=os.getenv("CELERY_BEAT_MAX_LOOP_INTERVAL", "5"),
    )
    scheduler_settings.ensure_by_key(
        db,
        key="beat_refresh_seconds",
        value_type=SettingValueType.integer,
        value_text=os.getenv("CELERY_BEAT_REFRESH_SECONDS", "30"),
    )
    scheduler_settings.ensure_by_key(
        db,
        key="refresh_minutes",
        value_type=SettingValueType.integer,
        value_text=os.getenv("CELERY_BEAT_REFRESH_MINUTES", "5"),
    )


def seed_auth_policy_settings(db: Session) -> None:
    auth_settings.ensure_by_key(
        db,
        key="default_auth_provider",
        value_type=SettingValueType.string,
        value_text=os.getenv("AUTH_DEFAULT_AUTH_PROVIDER", "local"),
    )
    auth_settings.ensure_by_key(
        db,
        key="default_session_status",
        value_type=SettingValueType.string,
        value_text=os.getenv("AUTH_DEFAULT_SESSION_STATUS", "active"),
    )


def seed_provisioning_settings(db: Session) -> None:
    provisioning_settings.ensure_by_key(
        db,
        key="nas_backup_retention_interval_seconds",
        value_type=SettingValueType.integer,
        value_text=os.getenv("NAS_BACKUP_RETENTION_INTERVAL", "86400"),
    )
    provisioning_settings.ensure_by_key(
        db,
        key="oauth_token_refresh_interval_seconds",
        value_type=SettingValueType.integer,
        value_text=os.getenv("OAUTH_TOKEN_REFRESH_INTERVAL", "86400"),
    )


def seed_projects_settings(db: Session) -> None:
    projects_settings.ensure_by_key(
        db,
        key="default_project_status",
        value_type=SettingValueType.string,
        value_text=os.getenv("PROJECTS_DEFAULT_PROJECT_STATUS", "planned"),
    )
    projects_settings.ensure_by_key(
        db,
        key="default_project_priority",
        value_type=SettingValueType.string,
        value_text=os.getenv("PROJECTS_DEFAULT_PROJECT_PRIORITY", "normal"),
    )
    projects_settings.ensure_by_key(
        db,
        key="default_task_status",
        value_type=SettingValueType.string,
        value_text=os.getenv("PROJECTS_DEFAULT_TASK_STATUS", "todo"),
    )
    projects_settings.ensure_by_key(
        db,
        key="default_task_priority",
        value_type=SettingValueType.string,
        value_text=os.getenv("PROJECTS_DEFAULT_TASK_PRIORITY", "normal"),
    )
    projects_settings.ensure_by_key(
        db,
        key="chart_config",
        value_type=SettingValueType.json,
        value_json={
            "type": "bar",
            "endpoint": "/api/v1/projects/charts/summary",
            "xKey": "status",
            "yKey": "count",
            "title": "Projects by Status",
            "label": "Projects",
            "options": "{}",
        },
    )
    projects_settings.ensure_by_key(
        db,
        key="kanban_config",
        value_type=SettingValueType.json,
        value_json={
            "endpoint": "/api/v1/projects/kanban",
            "updateEndpoint": "/api/v1/projects/kanban/move",
            "columnField": "status",
            "idField": "id",
            "titleField": "name",
            "subtitleField": "project_type",
            "metaFields": ["status", "due_date"],
        },
    )
    projects_settings.ensure_by_key(
        db,
        key="gantt_config",
        value_type=SettingValueType.json,
        value_json={
            "endpoint": "/api/v1/projects/gantt",
            "updateEndpoint": "/api/v1/projects/gantt/due-date",
            "idField": "id",
            "titleField": "name",
            "startField": "start_date",
            "dragField": "due_date",
        },
    )


def seed_workflow_settings(db: Session) -> None:
    workflow_settings.ensure_by_key(
        db,
        key="default_sla_clock_status",
        value_type=SettingValueType.string,
        value_text=os.getenv("WORKFLOW_DEFAULT_SLA_CLOCK_STATUS", "running"),
    )
    workflow_settings.ensure_by_key(
        db,
        key="default_sla_breach_status",
        value_type=SettingValueType.string,
        value_text=os.getenv("WORKFLOW_DEFAULT_SLA_BREACH_STATUS", "open"),
    )
    workflow_settings.ensure_by_key(
        db,
        key="sla_breach_detection_enabled",
        value_type=SettingValueType.boolean,
        value_text=os.getenv("SLA_BREACH_DETECTION_ENABLED", "true"),
    )
    workflow_settings.ensure_by_key(
        db,
        key="sla_breach_detection_interval_seconds",
        value_type=SettingValueType.integer,
        value_text=os.getenv("SLA_BREACH_DETECTION_INTERVAL_SECONDS", "1800"),
    )
    workflow_settings.ensure_by_key(
        db,
        key="ticket_auto_assignment_enabled",
        value_type=SettingValueType.boolean,
        value_text=os.getenv("TICKET_AUTO_ASSIGNMENT_ENABLED", "false"),
    )


def seed_network_policy_settings(db: Session) -> None:
    network_settings.ensure_by_key(
        db,
        key="default_device_type",
        value_type=SettingValueType.string,
        value_text=os.getenv("NETWORK_DEFAULT_DEVICE_TYPE", "ont"),
    )
    network_settings.ensure_by_key(
        db,
        key="default_device_status",
        value_type=SettingValueType.string,
        value_text=os.getenv("NETWORK_DEFAULT_DEVICE_STATUS", "active"),
    )
    network_settings.ensure_by_key(
        db,
        key="default_port_type",
        value_type=SettingValueType.string,
        value_text=os.getenv("NETWORK_DEFAULT_PORT_TYPE", "ethernet"),
    )
    network_settings.ensure_by_key(
        db,
        key="default_port_status",
        value_type=SettingValueType.string,
        value_text=os.getenv("NETWORK_DEFAULT_PORT_STATUS", "down"),
    )
    network_settings.ensure_by_key(
        db,
        key="default_ip_version",
        value_type=SettingValueType.string,
        value_text=os.getenv("NETWORK_DEFAULT_IP_VERSION", "ipv4"),
    )
    network_settings.ensure_by_key(
        db,
        key="default_olt_port_type",
        value_type=SettingValueType.string,
        value_text=os.getenv("NETWORK_DEFAULT_OLT_PORT_TYPE", "pon"),
    )
    network_settings.ensure_by_key(
        db,
        key="default_fiber_strand_status",
        value_type=SettingValueType.string,
        value_text=os.getenv("NETWORK_DEFAULT_FIBER_STRAND_STATUS", "available"),
    )
    network_settings.ensure_by_key(
        db,
        key="default_splitter_input_ports",
        value_type=SettingValueType.integer,
        value_text=os.getenv("NETWORK_DEFAULT_SPLITTER_INPUT_PORTS", "1"),
    )
    network_settings.ensure_by_key(
        db,
        key="default_splitter_output_ports",
        value_type=SettingValueType.integer,
        value_text=os.getenv("NETWORK_DEFAULT_SPLITTER_OUTPUT_PORTS", "8"),
    )
    # Fiber installation planning cost rates
    network_settings.ensure_by_key(
        db,
        key="fiber_drop_cable_cost_per_meter",
        value_type=SettingValueType.string,
        value_text=os.getenv("NETWORK_FIBER_DROP_CABLE_COST_PER_METER", "2.50"),
    )
    network_settings.ensure_by_key(
        db,
        key="fiber_labor_cost_per_meter",
        value_type=SettingValueType.string,
        value_text=os.getenv("NETWORK_FIBER_LABOR_COST_PER_METER", "1.50"),
    )
    network_settings.ensure_by_key(
        db,
        key="fiber_ont_device_cost",
        value_type=SettingValueType.string,
        value_text=os.getenv("NETWORK_FIBER_ONT_DEVICE_COST", "85.00"),
    )
    network_settings.ensure_by_key(
        db,
        key="fiber_installation_base_fee",
        value_type=SettingValueType.string,
        value_text=os.getenv("NETWORK_FIBER_INSTALLATION_BASE_FEE", "50.00"),
    )


def seed_network_settings(db: Session) -> None:
    kill_enabled_raw = os.getenv("NETWORK_MIKROTIK_SESSION_KILL_ENABLED", "true")
    network_settings.ensure_by_key(
        db,
        key="mikrotik_session_kill_enabled",
        value_type=SettingValueType.boolean,
        value_text=kill_enabled_raw,
        value_json=kill_enabled_raw.lower() in {"1", "true", "yes", "on"},
    )
    block_enabled_raw = os.getenv("NETWORK_ADDRESS_LIST_BLOCK_ENABLED", "true")
    network_settings.ensure_by_key(
        db,
        key="address_list_block_enabled",
        value_type=SettingValueType.boolean,
        value_text=block_enabled_raw,
        value_json=block_enabled_raw.lower() in {"1", "true", "yes", "on"},
    )
    network_settings.ensure_by_key(
        db,
        key="default_mikrotik_address_list",
        value_type=SettingValueType.string,
        value_text=os.getenv("NETWORK_DEFAULT_MIKROTIK_ADDRESS_LIST", ""),
    )


def seed_inventory_settings(db: Session) -> None:
    inventory_settings.ensure_by_key(
        db,
        key="default_reservation_status",
        value_type=SettingValueType.string,
        value_text=os.getenv("INVENTORY_DEFAULT_RESERVATION_STATUS", "active"),
    )
    inventory_settings.ensure_by_key(
        db,
        key="default_material_status",
        value_type=SettingValueType.string,
        value_text=os.getenv("INVENTORY_DEFAULT_MATERIAL_STATUS", "required"),
    )


def seed_comms_settings(db: Session) -> None:
    comms_settings.ensure_by_key(
        db,
        key="default_notification_status",
        value_type=SettingValueType.string,
        value_text=os.getenv("COMMS_DEFAULT_NOTIFICATION_STATUS", "pending"),
    )
    # Meta (Facebook/Instagram) Integration Settings
    comms_settings.ensure_by_key(
        db,
        key="meta_app_id",
        value_type=SettingValueType.string,
        value_text=os.getenv("META_APP_ID", ""),
    )
    comms_settings.ensure_by_key(
        db,
        key="meta_app_secret",
        value_type=SettingValueType.string,
        value_text=os.getenv("META_APP_SECRET", ""),
        is_secret=True,
    )
    comms_settings.ensure_by_key(
        db,
        key="meta_webhook_verify_token",
        value_type=SettingValueType.string,
        value_text=os.getenv("META_WEBHOOK_VERIFY_TOKEN", ""),
        is_secret=True,
    )
    comms_settings.ensure_by_key(
        db,
        key="meta_oauth_redirect_uri",
        value_type=SettingValueType.string,
        value_text=os.getenv("META_OAUTH_REDIRECT_URI", ""),
    )
    comms_settings.ensure_by_key(
        db,
        key="meta_graph_api_version",
        value_type=SettingValueType.string,
        value_text=os.getenv("META_GRAPH_API_VERSION", "v19.0"),
    )
    comms_settings.ensure_by_key(
        db,
        key="meta_access_token_override",
        value_type=SettingValueType.string,
        value_text=os.getenv("META_ACCESS_TOKEN_OVERRIDE", ""),
        is_secret=True,
    )
    comms_settings.ensure_by_key(
        db,
        key="company_name",
        value_type=SettingValueType.string,
        value_text=os.getenv("COMPANY_NAME", "Dotmac CRM"),
    )
