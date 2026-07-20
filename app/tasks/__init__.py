from app.services.workqueue.tasks import prune_snoozes, sla_tick
from app.tasks.bandwidth import (
    aggregate_to_metrics as aggregate_bandwidth_to_metrics,
)
from app.tasks.bandwidth import (
    cleanup_hot_data as cleanup_bandwidth_hot_data,
)
from app.tasks.bandwidth import (
    process_bandwidth_stream,
)
from app.tasks.bandwidth import (
    trim_redis_stream as trim_bandwidth_stream,
)
from app.tasks.campaigns import (
    execute_campaign,
    process_nurture_steps,
    process_scheduled_campaigns,
)
from app.tasks.crm_inbox import (
    cleanup_old_outbox_task,
    process_outbox_queue_task,
    reassign_stale_ai_handoffs_task,
    reopen_due_snoozed_conversations_task,
    send_outbound_message_task,
    send_outbox_item_task,
    send_reply_reminders_task,
)
from app.tasks.customer_retention import (
    reconcile_churning_retention_customers_to_selfcare,
    sync_lost_retention_customer_to_selfcare,
)
from app.tasks.field import prune_field_location_pings
from app.tasks.gis import sync_gis_sources
from app.tasks.infrastructure_health import run_infrastructure_health_checks
from app.tasks.integrations import (
    detect_dotmac_erp_identity_drift,
    redrive_failed_erp_pushes,
    refresh_material_request_erp_status,
    refresh_pending_material_request_erp_statuses,
    run_integration_job,
    sync_chatwoot,
    sync_dotmac_erp,
    sync_dotmac_erp_agents,
    sync_dotmac_erp_contacts,
    sync_dotmac_erp_entity,
    sync_dotmac_erp_inventory,
    sync_dotmac_erp_shifts,
    sync_dotmac_erp_teams,
    sync_dotmac_erp_technicians,
    sync_material_request_to_erp,
)
from app.tasks.intelligence import (
    capture_data_health_baseline,
    expire_stale_insights,
    invoke_persona_async,
    run_scheduled_analysis,
)
from app.tasks.notifications import deliver_notification_queue
from app.tasks.oauth import check_token_health, refresh_expiring_tokens
from app.tasks.performance import compute_weekly_scores, generate_flagged_reviews, update_goal_progress
from app.tasks.reports import run_weekly_inbound_reporting, send_scheduled_ncc_report
from app.tasks.subscriber_outreach import (
    resolve_stale_offline_outreach_conversations_task,
    run_daily_offline_outreach_task,
)
from app.tasks.subscribers import (
    reconcile_subscriber_identity,
    refresh_billing_risk_cache,
    refresh_retention_churn_detail_cache,
    sync_subscribers_from_selfcare,
    sync_subscribers_from_ucrm,
    sync_subscribers_generic,
)
from app.tasks.surveys import distribute_survey, process_survey_triggers
from app.tasks.webhooks import (
    deliver_webhook,
    process_email_webhook,
    process_meta_webhook,
    process_whatsapp_webhook,
    requeue_stale_pending_deliveries,
    retry_failed_deliveries,
)
from app.tasks.workflow import detect_sla_breaches, send_daily_sla_violation_report

__all__ = [
    "aggregate_bandwidth_to_metrics",
    "capture_data_health_baseline",
    "check_token_health",
    "cleanup_bandwidth_hot_data",
    "cleanup_old_outbox_task",
    "compute_weekly_scores",
    "deliver_notification_queue",
    "deliver_webhook",
    "detect_dotmac_erp_identity_drift",
    "detect_sla_breaches",
    "distribute_survey",
    "execute_campaign",
    "expire_stale_insights",
    "generate_flagged_reviews",
    "invoke_persona_async",
    "process_bandwidth_stream",
    "process_email_webhook",
    "process_meta_webhook",
    "process_nurture_steps",
    "process_outbox_queue_task",
    "process_scheduled_campaigns",
    "process_survey_triggers",
    "process_whatsapp_webhook",
    "prune_field_location_pings",
    "prune_snoozes",
    "reassign_stale_ai_handoffs_task",
    "reconcile_churning_retention_customers_to_selfcare",
    "reconcile_subscriber_identity",
    "redrive_failed_erp_pushes",
    "refresh_billing_risk_cache",
    "refresh_expiring_tokens",
    "refresh_material_request_erp_status",
    "refresh_pending_material_request_erp_statuses",
    "refresh_retention_churn_detail_cache",
    "reopen_due_snoozed_conversations_task",
    "requeue_stale_pending_deliveries",
    "resolve_stale_offline_outreach_conversations_task",
    "retry_failed_deliveries",
    "run_daily_offline_outreach_task",
    "run_infrastructure_health_checks",
    "run_integration_job",
    "run_scheduled_analysis",
    "run_weekly_inbound_reporting",
    "send_daily_sla_violation_report",
    "send_outbound_message_task",
    "send_outbox_item_task",
    "send_reply_reminders_task",
    "send_scheduled_ncc_report",
    "sla_tick",
    "sync_chatwoot",
    "sync_dotmac_erp",
    "sync_dotmac_erp_agents",
    "sync_dotmac_erp_contacts",
    "sync_dotmac_erp_entity",
    "sync_dotmac_erp_inventory",
    "sync_dotmac_erp_shifts",
    "sync_dotmac_erp_teams",
    "sync_dotmac_erp_technicians",
    "sync_gis_sources",
    "sync_lost_retention_customer_to_selfcare",
    "sync_material_request_to_erp",
    "sync_subscribers_from_selfcare",
    "sync_subscribers_from_ucrm",
    "sync_subscribers_generic",
    "trim_bandwidth_stream",
    "update_goal_progress",
]
